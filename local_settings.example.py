# Copy this file to local_settings.py and fill in real values.
# local_settings.py is gitignored (NVR password + POS api key must stay local).
# Alternatively set NVR_URL / POS_API_KEY as environment variables.

# NVR RTSP URL. Keep the literal {ch} placeholder -- the code substitutes the
# channel number per camera.
NVR_URL = "rtsp://USER:PASSWORD@192.168.1.70:554/user=USER&password=PASSWORD&channel={ch}"

# Shared secret for the POS Cloud Functions (header x-cctv-key). Get it from
# the POS team. Leave "" until the integration is wired up. Must MATCH the
# CCTV_API_KEY secret set on the POS side.
POS_API_KEY = ""

# Base URL of the POS Cloud Functions (where /cctvPresence, /cctvBookings,
# /cctvCorrections live). Get it from the POS team after deploy; leave "" until
# then. Form: https://asia-southeast1-<FIREBASE_PROJECT>.cloudfunctions.net
POS_API_BASE = ""
