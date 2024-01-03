#!/bin/bash

# exit when any command fails
set -e

cd "$(dirname "$0")/.."

# "app_name": "MonarchMoneyAmazonTagger",
# "author": "Jeff Prouty",
# "main_module": "src/main/python/monarchmoneyamazontagger/main.py",
# "version": "1.0.6",
# "gpg_key": "CB1608BF9A09BE99908045E6E93C791A0BFE386F",
# "gpg_name": "Jeff Prouty",
# "url": "https://github.com/jprouty/monarchmoney-amazon-tagger"
# "categories": "Utility;",
# "description": "Monarch Money Amazon tagger matches amazon purchases with your monarch transactions, giving them useful descriptions.",
# "author_email": "jeff.prouty@gmail.com",

echo "Clean everything"
python3 setup.py clean

echo "Setup the release venv"
python3 -m venv release_venv
source release_venv/bin/activate
pip install --upgrade pip
pip install --upgrade -r requirements/base.txt -r requirements/ubuntu.txt

pyinstaller \
  --name="MonarchMoneyAmazonTagger" \
  --windowed \
  --onefile \
  --icon=icons/Icon.ico \
  monarchmoneyamazontagger/main.py

echo "Now verify the built version works"
dist/MonarchMoneyAmazonTagger

deactivate
rm -rf release_venv

echo "TODO: Package as a .deb"
