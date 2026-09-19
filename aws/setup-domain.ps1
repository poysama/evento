# One-off: puts evento.peonbox.xyz in front of the evento-binder Lambda
# (ACM cert -> HTTP API -> custom domain -> Route53 alias). Safe to re-run.
$ErrorActionPreference = 'Stop'
$env:AWS_PAGER = ''
if (-not $env:AWS_PROFILE) { $env:AWS_PROFILE = 'default' }
$Region = 'ap-southeast-1'; $env:AWS_REGION = $Region
$Domain = 'evento.peonbox.xyz'; $Zone = 'peonbox.xyz'; $Fn = 'evento-binder'
$Acct   = (aws sts get-caller-identity --query Account --output text)

function Run { $out = & aws @args; if ($LASTEXITCODE -ne 0) { throw "aws $($args -join ' ') failed" }; $out }
function None($v) { -not $v -or $v -eq 'None' }

$ZoneId = (Run route53 list-hosted-zones-by-name --dns-name $Zone --query "HostedZones[0].Id" --output text) -replace '/hostedzone/', ''
function Record-Exists($name, $type) {
  $q = "ResourceRecordSets[?Name=='$name.' && Type=='$type'] | length(@)"
  [int](Run route53 list-resource-record-sets --hosted-zone-id $ZoneId --query $q --output text) -gt 0
}
function Create-Record($rrset) {
  # CREATE (never UPSERT) so an existing record can never be overwritten
  $f = Join-Path $env:TEMP 'evento-r53.json'
  @{ Changes = @(@{ Action = 'CREATE'; ResourceRecordSet = $rrset }) } | ConvertTo-Json -Depth 6 | Set-Content $f -Encoding ascii
  Run route53 change-resource-record-sets --hosted-zone-id $ZoneId --change-batch "file://$f" | Out-Null
}

# 1) certificate (DNS-validated in your zone)
$CertArn = Run acm list-certificates --query "CertificateSummaryList[?DomainName=='$Domain'].CertificateArn | [0]" --output text
if (None $CertArn) { $CertArn = Run acm request-certificate --domain-name $Domain --validation-method DNS --query CertificateArn --output text }
Write-Host "cert: $CertArn"
$rr = $null
for ($i = 0; $i -lt 30 -and -not $rr; $i++) {
  $v = Run acm describe-certificate --certificate-arn $CertArn --query "Certificate.DomainValidationOptions[0].ResourceRecord.[Name,Value]" --output text
  if (-not (None $v)) { $rr = $v -split "`t" } else { Start-Sleep 2 }
}
if (-not $rr) { throw 'ACM did not return a validation record' }
$vname = $rr[0].TrimEnd('.')
if (-not (Record-Exists $vname 'CNAME')) {
  Create-Record @{ Name = $rr[0]; Type = 'CNAME'; TTL = 300; ResourceRecords = @(@{ Value = $rr[1] }) }
  Write-Host "validation record created"
}
Write-Host 'waiting for certificate validation...'
Run acm wait certificate-validated --certificate-arn $CertArn

# 2) HTTP API in front of the Lambda
$FnArn = Run lambda get-function --function-name $Fn --query Configuration.FunctionArn --output text
$ApiId = Run apigatewayv2 get-apis --query "Items[?Name=='evento-binder'].ApiId | [0]" --output text
if (None $ApiId) { $ApiId = Run apigatewayv2 create-api --name evento-binder --protocol-type HTTP --target $FnArn --query ApiId --output text }
Write-Host "api: $ApiId"
try {
  Run lambda add-permission --function-name $Fn --statement-id apigw-invoke --action lambda:InvokeFunction `
    --principal apigateway.amazonaws.com --source-arn "arn:aws:execute-api:${Region}:${Acct}:${ApiId}/*/*" | Out-Null
} catch { Write-Host 'invoke permission already present' }
# rate limits: caps what a stranger hammering the URL can cost
Run apigatewayv2 update-stage --api-id $ApiId --stage-name '$default' --default-route-settings "ThrottlingBurstLimit=50,ThrottlingRateLimit=25" | Out-Null

# 3) custom domain + mapping
$dn = Run apigatewayv2 get-domain-names --query "Items[?DomainName=='$Domain'].DomainName | [0]" --output text
if (None $dn) {
  Run apigatewayv2 create-domain-name --domain-name $Domain --domain-name-configurations "CertificateArn=$CertArn,EndpointType=REGIONAL,SecurityPolicy=TLS_1_2" | Out-Null
}
$map = Run apigatewayv2 get-api-mappings --domain-name $Domain --query "Items[?ApiId=='$ApiId'] | length(@)" --output text
if ([int]$map -eq 0) { Run apigatewayv2 create-api-mapping --domain-name $Domain --api-id $ApiId --stage '$default' | Out-Null }
$cfg = (Run apigatewayv2 get-domain-name --domain-name $Domain --query "DomainNameConfigurations[0].[ApiGatewayDomainName,HostedZoneId]" --output text) -split "`t"

# 4) DNS alias evento.peonbox.xyz -> API Gateway
if (Record-Exists $Domain 'A') { Write-Host "A record for $Domain already exists - leaving it alone" }
else {
  Create-Record @{ Name = $Domain; Type = 'A'; AliasTarget = @{ HostedZoneId = $cfg[1]; DNSName = $cfg[0]; EvaluateTargetHealth = $false } }
  Write-Host "alias record created: $Domain -> $($cfg[0])"
}

# 5) only the custom domain is a way in
Run apigatewayv2 update-api --api-id $ApiId --disable-execute-api-endpoint | Out-Null
Write-Host "`nDONE: https://$Domain/"
