# Mount every image in $ImgDir with Mount-DiskImage, list it with
# tools/lister.py and with Get-ChildItem (independent second listing of the
# raw UTF-16 names), compare with the oracle, dismount.
param(
    [Parameter(Mandatory = $true)][string]$ImgDir,
    [Parameter(Mandatory = $true)][string]$OutDir,
    [string]$Subtrees = ''          # optional JSON file: {"image.iso": "/DIR/PATH"}
)
$ErrorActionPreference = 'Continue'
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$tools = Join-Path $PSScriptRoot '..\tools'

$cv = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion'
$sys = [ordered]@{
    ProductName    = $cv.ProductName
    DisplayVersion = $cv.DisplayVersion
    EditionID      = $cv.EditionID
    CurrentBuild   = $cv.CurrentBuild
    UBR            = $cv.UBR
    BuildLabEx     = $cv.BuildLabEx
    OSVersion      = [Environment]::OSVersion.VersionString
    cdfs_sys       = (Get-Item "$env:SystemRoot\System32\drivers\cdfs.sys" -ErrorAction SilentlyContinue).VersionInfo.FileVersion
    udfs_sys       = (Get-Item "$env:SystemRoot\System32\drivers\udfs.sys" -ErrorAction SilentlyContinue).VersionInfo.FileVersion
    python         = (& python --version 2>&1 | Out-String).Trim()
    pwsh           = $PSVersionTable.PSVersion.ToString()
}
$sys | ConvertTo-Json | Out-File -Encoding utf8 (Join-Path $OutDir 'sysinfo.json')
Get-Content (Join-Path $OutDir 'sysinfo.json')
& cmd /c ver | Out-File -Encoding utf8 (Join-Path $OutDir 'ver.txt')

$sub = @{}
if ($Subtrees -and (Test-Path $Subtrees)) {
    (Get-Content $Subtrees -Raw | ConvertFrom-Json).PSObject.Properties | ForEach-Object { $sub[$_.Name] = $_.Value }
}

function PsListing([string]$root, [string]$out) {
    # Raw UTF-16 code units of every name, via .NET enumeration.
    $items = @()
    try {
        Get-ChildItem -LiteralPath $root -Recurse -Force -ErrorAction Stop | ForEach-Object {
            $rel = $_.FullName.Substring($root.Length).TrimStart('\')
            $comps = @($rel -split '\\' | ForEach-Object { , @([int[]][char[]]$_ | ForEach-Object { '{0:x4}' -f $_ }) })
            $items += [ordered]@{ path_units = $comps; dir = $_.PSIsContainer; size = $(if ($_.PSIsContainer) { $null } else { $_.Length }) }
        }
        $err = $null
    } catch { $err = $_.Exception.ToString() }
    [ordered]@{ entries = $items; error = $err } | ConvertTo-Json -Depth 8 | Out-File -Encoding utf8 $out
}

foreach ($iso in Get-ChildItem -Path $ImgDir -Filter *.iso | Sort-Object Name) {
    $name = $iso.BaseName
    $d = Join-Path $OutDir $name
    New-Item -ItemType Directory -Force -Path $d | Out-Null
    Write-Host "=== $($iso.Name)"
    $mounted = $false
    try {
        $di = Mount-DiskImage -ImagePath $iso.FullName -StorageType ISO -Access ReadOnly -PassThru -ErrorAction Stop
        $mounted = $true
        $vol = $null
        for ($i = 0; $i -lt 30; $i++) {
            $vol = $di | Get-Volume -ErrorAction SilentlyContinue
            if ($vol -and $vol.DriveLetter) { break }
            Start-Sleep -Seconds 1
        }
        $di | Get-DiskImage | Select-Object * | ConvertTo-Json | Out-File -Encoding utf8 (Join-Path $d 'diskimage.json')
        if (-not $vol) { 'no volume appeared' | Out-File (Join-Path $d 'mount-error.txt'); continue }
        $vol | Select-Object DriveLetter, FileSystem, FileSystemType, FileSystemLabel, Size, SizeRemaining, HealthStatus, OperationalStatus, DriveType |
            ConvertTo-Json | Out-File -Encoding utf8 (Join-Path $d 'volume.json')
        Write-Host "  volume: $($vol.DriveLetter): FileSystem=$($vol.FileSystem) FileSystemType=$($vol.FileSystemType) Label=$($vol.FileSystemLabel)"
        if (-not $vol.DriveLetter) { 'volume has no drive letter' | Out-File (Join-Path $d 'mount-error.txt'); continue }
        $root = "$($vol.DriveLetter):\"
        & fsutil fsinfo volumeinfo "$($vol.DriveLetter):" 2>&1 | Out-File -Encoding utf8 (Join-Path $d 'fsutil.txt')
        $largs = @((Join-Path $tools 'lister.py'), $root, (Join-Path $d 'listing.json'))
        if ($sub.ContainsKey($iso.Name)) { $largs += @('--subtree', $sub[$iso.Name]) }
        & python @largs 2>&1 | Tee-Object -FilePath (Join-Path $d 'lister.log')
        if (-not $sub.ContainsKey($iso.Name)) { PsListing $root (Join-Path $d 'ps-listing.json') }
        & cmd /c "dir /s /a $root" 2>&1 | Out-File -Encoding utf8 (Join-Path $d 'dir.txt')
    } catch {
        $_ | Out-String | Out-File -Encoding utf8 (Join-Path $d 'mount-error.txt')
        Write-Host "  mount error: $_"
    } finally {
        if ($mounted) { Dismount-DiskImage -ImagePath $iso.FullName -ErrorAction SilentlyContinue | Out-Null }
    }
    $oracle = Join-Path $ImgDir "$name.oracle.json"
    $listing = Join-Path $d 'listing.json'
    if ((Test-Path $oracle) -and (Test-Path $listing)) {
        & python (Join-Path $tools 'compare.py') $oracle $listing | Out-File -Encoding utf8 (Join-Path $d 'compare.json')
        if (Test-Path (Join-Path $d 'ps-listing.json')) {
            & python (Join-Path $tools 'pscheck.py') $listing (Join-Path $d 'ps-listing.json') 2>&1 | Tee-Object -FilePath (Join-Path $d 'pscheck.txt')
        }
    }
}
$ctlO = Join-Path $ImgDir 'control-xorriso.oracle.json'
$ctlL = Join-Path $OutDir 'control-xorriso\listing.json'
if ((Test-Path $ctlO) -and (Test-Path $ctlL)) {
    & python (Join-Path $tools 'compare.py') --selftest $ctlO $ctlL 2>&1 | Tee-Object -FilePath (Join-Path $OutDir 'selftest.txt')
}
& python (Join-Path $tools 'summarise.py') $OutDir 2>&1 | Tee-Object -FilePath (Join-Path $OutDir 'summary.txt')
