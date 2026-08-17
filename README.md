# Elkjøp Stord Lageranalyse

Streamlit-app + lagerbot som analyserer Elkjøp-sortimentet og finner varer
Elkjøp Stord ikke har på lager, men som mange andre butikker har.

## Funksjoner

- automatisk henting og fornying av Algolia-nøkkel via Playwright
- kun produkter med `sellerName=Elkjøp`
- ekskluderer Phonehouse og Outlet fra sammenligningen
- kategori-filter
- merke-filter
- prioritet
- minimum prosent
- søk på SKU / produkt / merke / kategori
- klikkbar produktlenke
- eksport av filtrert CSV
- knapp for å kjøre ny analyse

## Lokal kjøring

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
streamlit run elkjop_app.py
```

## Deploy på Railway

Railway finner `Dockerfile` automatisk når den ligger i roten av GitHub-repoet.

1. Lag et nytt GitHub-repo.
2. Last opp alle filene i denne mappen.
3. Gå til Railway.
4. Velg **New Project → Deploy from GitHub repo**.
5. Velg repoet.
6. Vent på at Docker-bygget blir ferdig.
7. Under **Networking** oppretter du en offentlig domain/URL.

Appen bruker Railway sin `PORT` automatisk.

### Valgfritt passord

For å hindre at hvem som helst kjører analysen:

1. Railway → tjenesten → **Variables**
2. Legg til:

```text
APP_PASSWORD=velg-et-passord
```

Hvis `APP_PASSWORD` ikke finnes, er appen åpen.

## Viktig om lagring

CSV-filen lages på serverens lokale filsystem. Den fungerer mens instansen kjører,
men bør ikke regnes som permanent lagring etter restart/redeploy.

Botens hovedrapport heter:

```text
stord_mangler_elkjop.csv
```
