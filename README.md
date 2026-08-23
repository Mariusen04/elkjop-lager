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
- avkryssing av produkter og redigerbart bestillingsantall
- bestillingseksport som `SKU<TAB>antall`, klar for Mass Entry
- eksport av valgte eller alle filtrerte produkter
- knapp for å kjøre ny analyse
- tapsfri katalogpassering uten sekvensielle worker-prosesser
- analysen fortsetter på serveren selv om nettleseren lukkes
- analysestatus og rapport oppdateres automatisk mens jobben kjører
- beskytter mot flere samtidige analyser
- beholder forrige rapport til en ny rapport er ferdig
- støtte for persistent Railway-volume

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

### Eget domene

Du kan bruke et eget domene uten å flytte appen fra Railway:

1. Åpne tjenesten i Railway og gå til **Settings → Networking**.
2. Velg **+ Custom Domain** og skriv inn ønsket adresse, for eksempel
   `lager.dittdomene.no`.
3. Legg inn `CNAME`- og `TXT`-postene Railway viser hos DNS-leverandøren din.
4. Vent til domenet er verifisert. Railway oppretter og fornyer HTTPS-sertifikatet.

Et subdomene som `lager.dittdomene.no` er vanligvis enklest å konfigurere.

### Valgfritt passord

For å hindre at hvem som helst kjører analysen:

1. Railway → tjenesten → **Variables**
2. Legg til:

```text
APP_PASSWORD=velg-et-passord
APP_AUTH_SECRET=en-lang-tilfeldig-signeringnoekkel
```

Hvis `APP_PASSWORD` ikke finnes, er appen åpen. `APP_AUTH_SECRET` brukes til å
signere «Husk meg»-cookien, slik at passordet aldri lagres i nettleseren. Lag en
tilfeldig nøkkel med `openssl rand -hex 32`. Innloggingen huskes i 30 dager som
standard; dette kan endres med `APP_AUTH_COOKIE_DAYS`.

Tidspunkter vises i `Europe/Oslo`. Dette kan overstyres med for eksempel
`APP_TIMEZONE=Europe/Stockholm`.

### Permanent lagring

Uten et volume ligger rapporten på Railways midlertidige filsystem og kan forsvinne
ved en ny deploy. For å beholde rapporten:

1. Legg til et Railway-volume og monter det på `/data`.
2. Legg til denne variabelen under **Variables**:

```text
APP_DATA_DIR=/data
```

Appen lagrer deretter rapport, database, metadata og analyselogg på volumet. Uten
`APP_DATA_DIR` brukes appmappen som før.

## Rapportfil

Botens hovedrapport heter:

```text
stord_mangler_elkjop.csv
```

## Selvhosting på Orange Pi

Appen kan kjøres i Docker med en egen datamappe og automatisk omstart:

```bash
docker run -d \
  --name elkjop-lager \
  --restart unless-stopped \
  --env-file .env \
  -p 8080:8080 \
  -v "$PWD/data:/data" \
  elkjop-lager:latest
```

Eksempel på `.env`:

```text
APP_DATA_DIR=/data
APP_TIMEZONE=Europe/Oslo
TZ=Europe/Oslo
PORT=8080
APP_PASSWORD=velg-et-sterkt-passord
APP_AUTH_SECRET=lim-inn-resultatet-fra-openssl-rand-hex-32
APP_AUTH_COOKIE_DAYS=30
```

`Caddyfile` videresender både `ankervold.no` og `lager.ankervold.no` til appen,
sender `www.ankervold.no` videre til rotadressen og lar Caddy håndtere
HTTPS-sertifikatene automatisk. TCP-port 80 og 443 må peke fra ruteren til
Orange Pi-en.
