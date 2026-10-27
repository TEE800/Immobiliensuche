# Installiert die Mietsuche auf einem Windows-Laptop und startet sie bei
# jeder Anmeldung automatisch im Hintergrund. Kann erneut ausgeführt
# werden, um auf die neueste Version zu aktualisieren.
#
# Aufruf in PowerShell:
#   irm https://raw.githubusercontent.com/TEE800/Immobiliensuche/main/deploy/windows/install.ps1 | iex

$ErrorActionPreference = "Stop"
$Root = Join-Path $env:LOCALAPPDATA "Mietsuche"
$App = Join-Path $Root "app"
$Data = Join-Path $Root "data"
$Venv = Join-Path $Root "venv"
$TaskName = "Mietsuche"

function Step($text) { Write-Host "`n==> $text" -ForegroundColor Green }

Step "Python prüfen"
$py = Get-Command py -ErrorAction SilentlyContinue
if (-not $py) {
    Write-Host "Python wird installiert (einmalig, dauert ein paar Minuten) ..."
    winget install --id Python.Python.3.13 -e --silent --accept-package-agreements --accept-source-agreements
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
}

Step "Programm herunterladen"
New-Item -ItemType Directory -Force -Path $Root, $Data | Out-Null
$zip = Join-Path $env:TEMP "mietsuche.zip"
Invoke-WebRequest "https://github.com/TEE800/Immobiliensuche/archive/refs/heads/main.zip" -OutFile $zip
$unpacked = Join-Path $env:TEMP "mietsuche-src"
if (Test-Path $unpacked) { Remove-Item -Recurse -Force $unpacked }
Expand-Archive $zip -DestinationPath $unpacked
if (Test-Path $App) { Remove-Item -Recurse -Force $App }
Move-Item (Get-ChildItem $unpacked | Select-Object -First 1).FullName $App

Step "Abhängigkeiten installieren"
if (-not (Test-Path $Venv)) { py -3 -m venv $Venv }
& "$Venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
& "$Venv\Scripts\python.exe" -m pip install --quiet -r "$App\requirements.txt"

# Eigene Einstellungen und Anschreiben bleiben bei Updates erhalten.
if (-not (Test-Path "$Data\config.yaml")) { Copy-Item "$App\config.example.yaml" "$Data\config.yaml" }
if (-not (Test-Path "$Data\anschreiben.txt")) { Copy-Item "$App\anschreiben.example.txt" "$Data\anschreiben.txt" }

Step "Autostart einrichten"
$action = New-ScheduledTaskAction -Execute "$Venv\Scripts\pythonw.exe" `
    -Argument "-m mietsuche --serve --config `"$Data\config.yaml`"" -WorkingDirectory $App
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
Start-ScheduledTask -TaskName $TaskName

Step "Ruhezustand am Netzteil abschalten"
# Nur im Netzbetrieb, damit die Suche weiterläuft. Akku bleibt unverändert.
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0

Start-Sleep -Seconds 5
Start-Process "http://localhost:8080"
Write-Host "`nFertig! Die Mietsuche läuft jetzt im Hintergrund und startet bei jeder Anmeldung automatisch." -ForegroundColor Green
Write-Host "Übersicht: http://localhost:8080"
Write-Host "Einstellungen: $Data"
