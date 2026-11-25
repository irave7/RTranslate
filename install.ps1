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
        $client = [Net.WebClient]::new()
        try {
            $client.DownloadFile("$url/rtranslate.zip", $Archive)
            $checks = $client.DownloadString("$url/SHA256SUMS.txt")
        } finally { $client.Dispose() }
        if ($checks -notmatch '(?m)^([a-fA-F0-9]{64}) +rtranslate\.zip\r?$') {
            throw 'The release does not identify the RTranslate ZIP checksum.'
        }
        $Sha256 = $Matches[1]
    }
    $stream = [IO.File]::OpenRead([IO.Path]::GetFullPath($Archive))
    $sha = [Security.Cryptography.SHA256]::Create()
    try { $actual = [BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-', '') }
    finally { $stream.Dispose(); $sha.Dispose() }
    if ($actual -ne $Sha256) {
        throw 'RTranslate ZIP checksum mismatch.'
    }
    $skill = Join-Path $scratch 'skill'
    [void][Reflection.Assembly]::LoadWithPartialName('System.IO.Compression.FileSystem')
    [IO.Compression.ZipFile]::ExtractToDirectory([IO.Path]::GetFullPath($Archive), $skill)
    $installArgs = @('--install-skill', '--host', $TargetHost)
    if ($Project) { $installArgs += @('--project', $Project) }
    & (Join-Path $skill 'scripts/rtranslate.cmd') @installArgs
    if ($LASTEXITCODE -ne 0) { throw "RTranslate installation failed ($LASTEXITCODE)." }
} finally {
    if (Test-Path -LiteralPath $scratch) { Remove-Item -LiteralPath $scratch -Recurse -Force }
}
