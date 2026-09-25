# NSFW Mask (Hardened) - Windows installer
#
# Run it via install_windows.bat (double-click) so the execution policy and
# error pauses are handled for you. Running the .ps1 directly from Explorer
# may be blocked by the default PowerShell execution policy.
#
# Messages are kept ASCII-only on purpose: Windows PowerShell 5.1 reads .ps1
# files using the ANSI codepage unless a BOM is present.
#
# Differences from the original installer:
#   - No API key is written into user-level environment variables in plaintext.
#     The application generates a random key into data\api_key with restricted
#     permissions on first start.
#   - No default key is baked in anywhere, so an unconfigured install is not
#     silently open to everyone.
#   - The service binds to 127.0.0.1 by default.

$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

function Fail([string]$msg) {
    # Red error + pause so the window never closes before the message is read.
    Write-Host ""
    Write-Host "ERROR: $msg" -ForegroundColor Red
    Write-Host ""
    Read-Host "Press Enter to exit"
    exit 1
}

function Step([string]$msg) {
    Write-Host ""
    Write-Host "==> $msg" -ForegroundColor Cyan
}

Write-Host "------------------------------------------------"
Write-Host " NSFW Mask (Hardened) - Windows installer"
Write-Host " Project: $projectRoot"
Write-Host "------------------------------------------------"

try {
    # --- 1. Python -----------------------------------------------------------
    Step "Looking for Python 3.10+ ..."
    $python = $null
    foreach ($candidate in @("py -3", "python", "python3")) {
        $parts = $candidate.Split(" ")
        $exe = $parts[0]
        if (Get-Command $exe -ErrorAction SilentlyContinue) {
            try {
                $version = & $exe $parts[1..($parts.Length - 1)] -c "import sys;print('%d.%d'%sys.version_info[:2])" 2>$null
                if ($version -and [version]("$version") -ge [version]"3.10") {
                    $python = $candidate
                    Write-Host "    Found Python $version via '$candidate'"
                    break
                } elseif ($version) {
                    Write-Host "    Skipping '$candidate' (Python $version < 3.10)"
                }
            } catch { }
        }
    }
    if (-not $python) {
        Fail ("Python 3.10+ not found on PATH. Install it from https://www.python.org/downloads/ " +
              "and tick 'Add python.exe to PATH' in the installer, then run this again.")
    }

    # --- 2. Virtual environment ---------------------------------------------
    Step "Creating virtual environment (venv) ..."
    # Always build into <project>\venv regardless of the current directory.
    $venvDir = Join-Path $projectRoot "venv"
    $parts = $python.Split(" ")
    & $parts[0] $parts[1..($parts.Length - 1)] -m venv "$venvDir"
    if ($LASTEXITCODE -ne 0) { Fail "Failed to create the virtual environment (exit code $LASTEXITCODE)." }
    $venvPython = Join-Path $venvDir "Scripts\python.exe"
    if (-not (Test-Path $venvPython)) { Fail "venv python.exe not found at $venvPython" }
    Write-Host "    venv ready: $venvDir"

    # --- 3. Dependencies -----------------------------------------------------
    Step "Choosing dependency set ..."
    $choice = $Host.UI.PromptForChoice(
        "Hardware",
        "Do you have an NVIDIA GPU with a recent driver?",
        [System.Management.Automation.Host.ChoiceDescription[]]@(
            (New-Object System.Management.Automation.Host.ChoiceDescription "&Yes", "Install GPU build"),
            (New-Object System.Management.Automation.Host.ChoiceDescription "&No",  "Install CPU build")
        ),
        1
    )

    Step "Installing dependencies (this can take several minutes) ..."
    Push-Location $projectRoot
    try {
        if ($choice -eq 0) {
            & $venvPython -m pip install -r requirements-gpu.txt
        } else {
            & $venvPython -m pip install -r requirements.txt
        }
        if ($LASTEXITCODE -ne 0) {
            Fail "pip install failed (exit code $LASTEXITCODE). Check your network/proxy and run this again."
        }

        # --- 4. API key ------------------------------------------------------
        Step "Generating API key ..."
        $keyScript = "from backend import config; from backend.security import SecretStore; config.ensure_dirs(); SecretStore(config.API_KEY_FILE, config.SIGNING_KEY_FILE).api_key"
        & $venvPython -c $keyScript | Out-Null
        if ($LASTEXITCODE -ne 0) { Fail "API key generation failed (exit code $LASTEXITCODE)." }

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
                Write-Host "    API key written to: $keyFile (ACL restricted to $env:USERNAME)"
            } catch {
                Write-Warning "Could not tighten ACL on $keyFile - please check it manually."
            }
        }

        # --- 5. Done ---------------------------------------------------------
        Write-Host ""
        Write-Host "[4/4] Done."
        Write-Host "------------------------------------------------"
        Write-Host "Next steps"
        Write-Host "  1. Read your API key:   type `"$keyFile`""
        Write-Host "  2. Start the server:    double-click start.bat in the project folder"
        Write-Host "  3. Open http://127.0.0.1:8000 and paste the key."
        Write-Host ""
        Write-Host "  To expose it on the LAN, set NSFW_HOST=0.0.0.0 and put a"
        Write-Host "  reverse proxy with TLS in front. Do not expose it directly."
        Write-Host "------------------------------------------------"
    } finally {
        Pop-Location
    }
} catch {
    Fail ("Unexpected error: " + $_.Exception.Message)
}
Read-Host "Install finished. Press Enter to exit"
