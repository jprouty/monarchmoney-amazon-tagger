$scriptpath = $MyInvocation.MyCommand.Path
$dir = Split-Path $scriptpath
Push-Location $dir\..

python setup.py clean

echo "Setup the release venv"
python -m venv release_venv
.\release_venv\Scripts\activate.ps1
pip install --upgrade pip
pip install --upgrade -r requirements/base.txt -r requirements/windows.txt

echo "Install PyInstaller locally, with a locally built bootloader. This helps avoid any anti-virus conflation with other PyInstaller apps from the publicly built version."
# See more here: https://stackoverflow.com/questions/43777106/program-made-with-pyinstaller-now-seen-as-a-trojan-horse-by-avg
$pyinstaller = Join-Path 'C:\' $(New-Guid) | %{ mkdir $_ }
Push-Location $pyinstaller

$PyInstallerArchiveUrl = "https://github.com/pyinstaller/pyinstaller/archive/refs/tags/v5.0.zip"
$PyInstallerLocalZip = Join-Path $pyinstaller 'PyInstaller_v5.0.zip'
Invoke-WebRequest -OutFile $PyInstallerLocalZip $PyInstallerArchiveUrl
$PyInstallerLocalZip | Expand-Archive -DestinationPath $pyinstaller -Force

Push-Location pyinstaller-5.0
Push-Location bootloader
python ./waf all
Pop-Location
python setup.py install
Pop-Location
Pop-Location

echo "Build it"
pyinstaller `
  --name="MonarchMoneyAmazonTagger" `
  --onefile `
  --windowed `
  --icon .\icons\Icon.ico `
  .\monarchmoneyamazontagger\main.py

echo "Signing the app"
$password = Get-Content .\sign\password -Raw
signtool sign `
  /f .\sign\certificate.pfx `
  /p $password.trim() `
  /fd sha256 `
  /d "Monarch Money Amazon tagger matches amazon purchases with your monarch transactions, giving them useful descriptions."  `
  /du "https://github.com/jprouty/monarchmoney-amazon-tagger" `
  /tr http://timestamp.sectigo.com `
  /td sha256  `
  .\dist\MonarchMoneyAmazonTagger.exe

echo "Now verify the built version works"
.\dist\MonarchMoneyAmazonTagger.exe | Out-Null

deactivate
Remove-Item -Recurse -Force .\release_venv\
Remove-Item -Recurse -Force $pyinstaller
Pop-Location
