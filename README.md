# WhatsApp RH Bot — Big Mamma Group

Chatbot WhatsApp pour les questions RH des employés Big Mamma.
Recherche dans le hub RH Notion et répond en français ou italien via Claude.

## Architecture

```
WhatsApp → Twilio → Flask webhook (/webhook)
                        ├→ notion_search.py  (Notion API)
                        ├→ Claude API        (generate answer)
                        └→ Twilio TwiML      (reply on WhatsApp)
```

## Setup local

```bash
cd whatsapp-rh-bot
python -m venv .venv
source .venv/Scripts/activate  # Windows
pip install -r requirements.txt

cp .env.example .env
# Fill in the values in .env
```

Pour tester localement avec Twilio, utilise ngrok :

```bash
python app.py                        # starts on port 5000
ngrok http 5000                      # in another terminal
# Copy the ngrok HTTPS URL → Twilio console webhook
```

## Déploiement sur Railway

### 1. Créer le projet

1. Va sur [railway.app](https://railway.app) et connecte-toi avec GitHub
2. **New Project → Deploy from GitHub repo**
3. Sélectionne le repo contenant `whatsapp-rh-bot`
4. Railway détecte automatiquement le `Procfile`

### 2. Variables d'environnement

Dans Railway → ton service → **Variables**, ajoute :

| Variable | Source |
|---|---|
| `TWILIO_ACCOUNT_SID` | [Twilio Console](https://console.twilio.com/) |
| `TWILIO_AUTH_TOKEN` | Twilio Console |
| `TWILIO_WHATSAPP_NUMBER` | Format `whatsapp:+14155238886` |
| `NOTION_TOKEN` | [Notion Integrations](https://www.notion.so/my-integrations) |
| `NOTION_PAGE_ID` | `34c5d2b79c458197986ce0a69e6c053f` |
| `ANTHROPIC_API_KEY` | [Anthropic Console](https://console.anthropic.com/) |

### 3. Configurer Notion

1. Va sur [notion.so/my-integrations](https://www.notion.so/my-integrations)
2. Crée une intégration (nom : `WhatsApp RH Bot`)
3. Copie le token → variable `NOTION_TOKEN`
4. Ouvre la page HR hub dans Notion → **⋯ → Connexions → Connecter** → sélectionne l'intégration
5. Coche **inclure les sous-pages** pour donner accès à tout le hub

### 4. Configurer Twilio

1. Crée un compte [Twilio](https://www.twilio.com/) si nécessaire
2. Active le **Twilio Sandbox for WhatsApp** (pour le dev) :
   - Console → Messaging → Try it out → Send a WhatsApp message
   - Envoie le code d'activation depuis ton téléphone
3. Configure le webhook :
   - Console → Messaging → Try it out → Send a WhatsApp message
   - **When a message comes in** : `https://ton-app.up.railway.app/webhook`
   - Méthode : **POST**

### 5. Déployer

Railway déploie automatiquement à chaque push. Vérifie :

```
https://ton-app.up.railway.app/health  → {"status": "ok"}
```

### 6. Passer en production

Pour un numéro WhatsApp dédié (hors sandbox) :
1. Twilio Console → Messaging → Senders → WhatsApp Senders
2. Soumets une demande avec le profil business Big Mamma
3. Une fois approuvé, mets à jour `TWILIO_WHATSAPP_NUMBER`

## Tester

Envoie un message WhatsApp au numéro sandbox/prod :

- "Comment poser mes congés ?"
- "Come funziona il contratto di prova?"
- "C'est quoi la mutuelle ?"

Le bot cherche dans Notion et répond dans la langue détectée.
