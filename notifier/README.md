# notifier

Webhook-to-UI broadcast service. Receives `POST /notifier/notify` from any subservice and delivers toast notifications to all connected browser tabs via SSE.

use command to test

curl -X POST http://localhost:8888/notifier/notify \
    -H 'Content-Type: application/json' \
    -d '{"title":"Export done","message":"Your dataset is ready","level":"success","action_label":"Download","action_url":"/databridge/"}'