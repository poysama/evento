# Deploys (or updates) the Evento binder to AWS: one Lambda + one private S3 bucket (+ card art upload).
# Usage:  powershell -File aws\deploy.ps1            (uses AWS_PROFILE, default "default")
$ErrorActionPreference = 'Stop'
$env:AWS_PAGER = ''
if (-not $env:AWS_PROFILE) { $env:AWS_PROFILE = 'default' }
$Region  = 'ap-southeast-1'; $env:AWS_REGION = $Region
$App     = Split-Path -Parent $PSScriptRoot
$Fn      = 'evento-binder'
$Role    = 'evento-binder-role'
$Acct    = (aws sts get-caller-identity --query Account --output text)
$Bucket  = "evento-binder-$Acct"
$PassFile = Join-Path $PSScriptRoot 'passcode.txt'

function Run { $out = & aws @args; if ($LASTEXITCODE -ne 0) { throw "aws $($args -join ' ') failed" }; $out }
function Exists { param([scriptblock]$s) try { & $s *> $null; return ($LASTEXITCODE -eq 0) } catch { return $false } }

# --- passcode (generated once, kept locally, never committed)
if (-not (Test-Path $PassFile)) {
  $chars = [char[]]'abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789'
  $rng = [Security.Cryptography.RandomNumberGenerator]::Create(); $b = New-Object byte[] 24; $rng.GetBytes($b)
  ($b | ForEach-Object { $chars[$_ % $chars.Length] }) -join '' | Set-Content $PassFile -NoNewline -Encoding ascii
}
$Pass = (Get-Content $PassFile -Raw).Trim()

# --- private bucket
if (-not (Exists { aws s3api head-bucket --bucket $Bucket })) {
  Run s3api create-bucket --bucket $Bucket --region $Region --create-bucket-configuration "LocationConstraint=$Region" | Out-Null
}
Run s3api put-public-access-block --bucket $Bucket --public-access-block-configuration "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"
# used passkey challenges (used/) only need to live a day; keeps the bucket tidy
$lc = '{"Rules":[{"ID":"expire-used-challenges","Status":"Enabled","Filter":{"Prefix":"used/"},"Expiration":{"Days":1}}]}'
$lcFile = Join-Path $env:TEMP 'evento-lifecycle.json'; $lc | Set-Content $lcFile -Encoding ascii
Run s3api put-bucket-lifecycle-configuration --bucket $Bucket --lifecycle-configuration "file://$lcFile"
Write-Host "bucket ready: $Bucket (public access blocked)"

# --- card art (private; only the Lambda can read it)
if (Test-Path "$App\card_images") {
  Run s3 sync "$App\card_images" "s3://$Bucket/images/" --exclude "*" --include "*.jpg" --only-show-errors
  Write-Host "English images synced"
}
if (Test-Path "$App\card_images_jp") {
  Run s3 sync "$App\card_images_jp" "s3://$Bucket/images_jp/" --exclude "*" --include "*.png" --include "manifest.json" --only-show-errors
  Write-Host "Japanese images synced"
}

# --- execution role (logs + read/write this bucket only)
$trust = '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
$trustFile = Join-Path $env:TEMP 'evento-trust.json'; $trust | Set-Content $trustFile -Encoding ascii
$newRole = -not (Exists { aws iam get-role --role-name $Role })
if ($newRole) { Run iam create-role --role-name $Role --assume-role-policy-document "file://$trustFile" | Out-Null }
Run iam attach-role-policy --role-name $Role --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
$pol = @{Version='2012-10-17';Statement=@(@{Effect='Allow';Action=@('s3:GetObject','s3:PutObject');Resource="arn:aws:s3:::$Bucket/*"})} | ConvertTo-Json -Depth 5 -Compress
$polFile = Join-Path $env:TEMP 'evento-s3.json'; $pol | Set-Content $polFile -Encoding ascii
Run iam put-role-policy --role-name $Role --policy-name evento-bucket-access --policy-document "file://$polFile"
$RoleArn = Run iam get-role --role-name $Role --query Role.Arn --output text
if ($newRole) { Write-Host 'waiting for IAM to propagate...'; Start-Sleep 12 }

# --- package
$zip = Join-Path $PSScriptRoot 'evento-lambda.zip'; if (Test-Path $zip) { Remove-Item $zip }
$stage = Join-Path $env:TEMP 'evento-stage'; if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
New-Item -ItemType Directory $stage | Out-Null
# runtime deps (passkey crypto) as Linux / Python 3.12 wheels - what Lambda runs, whatever this PC is
$py = if (Get-Command py -ErrorAction SilentlyContinue) { @('py', '-3') } else { @('python') }
& $py[0] $py[1..($py.Length - 1)] -m pip install --quiet --target $stage -r "$PSScriptRoot\requirements.txt" `
  --platform manylinux_2_28_x86_64 --platform manylinux2014_x86_64 --python-version 3.12 --implementation cp --only-binary=:all:
if ($LASTEXITCODE -ne 0) { throw 'pip install of Lambda dependencies failed' }
Copy-Item "$PSScriptRoot\lambda_function.py","$App\index.html","$App\cards.json" $stage
Get-ChildItem $stage -Recurse -Directory -Filter __pycache__ | Remove-Item -Recurse -Force
Compress-Archive -Path "$stage\*" -DestinationPath $zip

# --- function
if (-not (Exists { aws lambda get-function --function-name $Fn })) {
  Run lambda create-function --function-name $Fn --runtime python3.12 --handler lambda_function.handler `
    --role $RoleArn --zip-file "fileb://$zip" --timeout 10 --memory-size 256 `
    --environment "Variables={BUCKET=$Bucket,PASSCODE=$Pass}" | Out-Null
  Run lambda wait function-active-v2 --function-name $Fn
} else {
  Run lambda update-function-code --function-name $Fn --zip-file "fileb://$zip" | Out-Null
  Run lambda wait function-updated-v2 --function-name $Fn
  Run lambda update-function-configuration --function-name $Fn --environment "Variables={BUCKET=$Bucket,PASSCODE=$Pass}" | Out-Null
  Run lambda wait function-updated-v2 --function-name $Fn
}

# --- public access is via API Gateway + evento.peonbox.xyz (see setup-domain.ps1); no raw Function URL.
Write-Host "`nDeployed. Site: https://evento.peonbox.xyz/  (first time? run aws\setup-domain.ps1 once)"
Write-Host "Passcode is in: $PassFile"
