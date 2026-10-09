#!/usr/bin/env bash
# Publica o CRM no Firebase Hosting (site crm-vertical-seguros) e, se pedido, as regras do Firestore.
#   ./deploy.sh            -> só o site
#   ./deploy.sh --regras   -> site + regras do Firestore
set -e
cd "$(dirname "$0")"
rm -rf site && mkdir site
cp crmbatalha.html site/index.html
cp crmbatalha.html site/crmbatalha.html
if [ "$1" = "--regras" ]; then
  firebase deploy --only hosting:crm,firestore:rules --project gc---vertical-seguros
else
  firebase deploy --only hosting:crm --project gc---vertical-seguros
fi
