# One-off: lets GitHub Actions on the main branch of poysama/evento deploy the Lambda,
# via short-lived OIDC credentials (no AWS keys are stored in GitHub). Safe to re-run.
$ErrorActionPreference = 'Stop'
$env:AWS_PAGER = ''
if (-not $env:AWS_PROFILE) { $env:AWS_PROFILE = 'default' }
$Region = 'ap-southeast-1'; $env:AWS_REGION = $Region
$Repo = 'poysama/evento'; $Fn = 'evento-binder'; $Role = 'evento-github-deploy'
$Acct = (aws sts get-caller-identity --query Account --output text)
function Run { $out = & aws @args; if ($LASTEXITCODE -ne 0) { throw "aws $($args -join ' ') failed" }; $out }

# reuse the account's existing GitHub OIDC provider (it is one-per-account); create only if missing
$Oidc = "arn:aws:iam::${Acct}:oidc-provider/token.actions.githubusercontent.com"
$have = Run iam list-open-id-connect-providers --query "OpenIDConnectProviderList[?Arn=='$Oidc'] | length(@)" --output text
if ([int]$have -eq 0) {
  Run iam create-open-id-connect-provider --url https://token.actions.githubusercontent.com --client-id-list sts.amazonaws.com | Out-Null
}

# trust: ONLY workflows running on refs/heads/main of this one repo
$trust = @{Version='2012-10-17';Statement=@(@{
  Effect='Allow'; Principal=@{Federated=$Oidc}; Action='sts:AssumeRoleWithWebIdentity'
  Condition=@{StringEquals=@{
    'token.actions.githubusercontent.com:aud'='sts.amazonaws.com'
    'token.actions.githubusercontent.com:sub'="repo:${Repo}:ref:refs/heads/main" }}})} | ConvertTo-Json -Depth 8 -Compress
$tf = Join-Path $env:TEMP 'evento-ci-trust.json'; $trust | Set-Content $tf -Encoding ascii
$exists = try { & aws iam get-role --role-name $Role *> $null; $LASTEXITCODE -eq 0 } catch { $false }
if ($exists) { Run iam update-assume-role-policy --role-name $Role --policy-document "file://$tf" }
else { Run iam create-role --role-name $Role --assume-role-policy-document "file://$tf" --max-session-duration 3600 | Out-Null }

# permissions: update this one function's code, nothing else
$pol = @{Version='2012-10-17';Statement=@(@{
  Effect='Allow'
  Action=@('lambda:UpdateFunctionCode','lambda:GetFunction','lambda:GetFunctionConfiguration')
  Resource="arn:aws:lambda:${Region}:${Acct}:function:${Fn}" })} | ConvertTo-Json -Depth 6 -Compress
$pf = Join-Path $env:TEMP 'evento-ci-policy.json'; $pol | Set-Content $pf -Encoding ascii
Run iam put-role-policy --role-name $Role --policy-name deploy-evento-lambda --policy-document "file://$pf"

$Arn = Run iam get-role --role-name $Role --query Role.Arn --output text
Write-Host "deploy role: $Arn"
# the ARN is stored as a repo *variable* so the account id isn't hard-coded in a public repo
gh variable set AWS_DEPLOY_ROLE_ARN --body $Arn --repo $Repo
Write-Host "GitHub repo variable AWS_DEPLOY_ROLE_ARN set"
