[CmdletBinding()]
param(
    [ValidateSet('shared', 'workbuddy')] [string] $TargetHost = 'shared',
    [string] $Project,
    [string] $Archive,
    [string] $Sha256
)
$ErrorActionPreference = 'Stop'
$version = 'v1.0.0'
$scratch = Join-Path ([IO.Path]::GetTempPath()) ('rtranslate-install-' + [Guid]::NewGuid())
[void][IO.Directory]::CreateDirectory($scratch)
try {
    if (-not $Archive) {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        $url = "https://github.com/irave7/RTranslate/releases/download/$version"
        $Archive = Join-Path $scratch 'rtranslate.zip'
        $client = New-Object Net.WebClient
        try {
            $client.DownloadFile("$url/rtranslate.zip", $Archive)
            $checks = $client.DownloadString("$url/SHA256SUMS.txt")
        } finally { $client.Dispose() }
        if ($checks -notmatch '(?m)^([a-fA-F0-9]{64}) +rtranslate\.zip\r?$') {
            throw 'The release does not identify the RTranslate ZIP checksum.'
        }
        $Sha256 = $Matches[1]
    }
    if ((Get-FileHash -Algorithm SHA256 -LiteralPath $Archive).Hash -ne $Sha256) {
        throw 'RTranslate ZIP checksum mismatch.'
    }
    $skill = Join-Path $scratch 'skill'
    Expand-Archive -LiteralPath $Archive -DestinationPath $skill
    $installArgs = @('--install-skill', '--host', $TargetHost)
    if ($Project) { $installArgs += @('--project', $Project) }
    & (Join-Path $skill 'scripts/rtranslate.cmd') @installArgs
    if ($LASTEXITCODE -ne 0) { throw "RTranslate installation failed ($LASTEXITCODE)." }
} finally {
    if (Test-Path -LiteralPath $scratch) { Remove-Item -LiteralPath $scratch -Recurse -Force }
}
