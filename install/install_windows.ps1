# NSFW Mask (Hardened) - Windows installer
#
# Messages are kept ASCII-only on purpose: Windows PowerShell 5.1 reads .ps1
# files using the ANSI codepage unless a BOM is present, which would garble
# non-ASCII text.
#
# Differences from the original installer:
#   - No API key is written into user-level environment variables in plaintext.
#     The application generates a random key into data\api_key with restricted
#     permissions on first start.
#   - No default key is baked in anywhere, so an unconfigured install is not
#     silently open to everyone.
#   - The service binds to 127.0.0.1 by default.

$ErrorActionPreference = "Stop"

Write-Host "------------------------------------------------"
Write-Host " NSFW Mask (Hardened) - Windows installer"
Write-Host "------------------------------------------------"

# --- 1. Python ---------------------------------------------------------------
$python = $null
foreach ($candidate in @("py -3", "python", "python3")) {
    $parts = $candidate.Split(" ")
    $exe = $parts[0]
    if (Get-Command $exe -ErrorAction SilentlyContinue) {
        try {
            $version = & $exe $parts[1..($parts.Length - 1)] -c "import sys;print('%d.%d'%sys.version_info[:2])" 2>$null
            if ($version -and [version]$version -ge [version]"3.10") {
                $python = $candidate
                Write-Host ">> Found Python $version via '$candidate'"
                break
            }
        } catch { }
    }
}
if (-not $python) {
    Write-Error "Python 3.10+ not found. Install it from https://www.python.org/downloads/"
    exit 1
}

# --- 2. Virtual environment --------------------------------------------------
Write-Host "[1/4] Creating virtual environment (venv)..."
$parts = $python.Split(" ")
& $parts[0] $parts[1..($parts.Length - 1)] -m venv venv
$venvPython = Join-Path $PSScriptRoot "..\venv\Scripts\python.exe"
$venvPython = (Resolve-Path $venvPython).Path

# --- 3. Dependencies ---------------------------------------------------------
Write-Host "[2/4] Installing dependencies..."
$choice = $Host.UI.PromptForChoice(
    "Hardware",
    "Do you have an NVIDIA GPU with a recent driver?",
    [System.Management.Automation.Host.ChoiceDescription[]]@(
        (New-Object System.Management.Automation.Host.ChoiceDescription "&Yes", "Install GPU build"),
        (New-Object System.Management.Automation.Host.ChoiceDescription "&No",  "Install CPU build")
    ),
    1
)

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Push-Location $projectRoot
try {
    if ($choice -eq 0) {
        & $venvPython -m pip install -r requirements-gpu.txt
    } else {
        & $venvPython -m pip install -r requirements.txt
    }

    # --- 4. API key ----------------------------------------------------------
    Write-Host "[3/4] Generating API key..."
    $keyScript = "from backend import config; from backend.security import SecretStore; config.ensure_dirs(); SecretStore(config.API_KEY_FILE, config.SIGNING_KEY_FILE).api_key"
    & $venvPython -c $keyScript | Out-Null

    $keyFile = Join-Path $projectRoot "data\api_key"
    if (Test-Path $keyFile) {
        # Tighten the ACL to the current user only.
        try {
            $acl = Get-Acl $keyFile
            $acl.SetAccessRuleProtection($true, $false)
            $acl.Access | ForEach-Object { $acl.RemoveAccessRule($_) | Out-Null }
            $rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
                "$env:USERNAME", "Read,Write", "Allow")
            $acl.AddAccessRule($rule)
            Set-Acl -Path $keyFile -AclObject $acl
            Write-Host ">> API key written to: $keyFile (ACL restricted to $env:USERNAME)"
        } catch {
            Write-Warning "Could not tighten ACL on $keyFile - please check it manually."
        }
    }

    # --- 5. Done -------------------------------------------------------------
    Write-Host "[4/4] Done."
    Write-Host "------------------------------------------------"
    Write-Host "Next steps"
    Write-Host "  1. Read your API key:   type `"$keyFile`""
    Write-Host "  2. Start the server:"
    Write-Host "     $venvPython -m uvicorn backend.app:app --host 127.0.0.1 --port 8000"
    Write-Host "  3. Open http://127.0.0.1:8000 and paste the key."
    Write-Host ""
    Write-Host "  To expose it on the LAN, set NSFW_HOST=0.0.0.0 and put a"
    Write-Host "  reverse proxy with TLS in front. Do not expose it directly."
    Write-Host "------------------------------------------------"
}
finally {
    Pop-Location
}
