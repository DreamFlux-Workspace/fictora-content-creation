"""Local post on the operator's laptop: voice change, sound effects, music, look, mix, captions, mark.

Hosted post-production is off on the Drama API, so a take is finished here with
ffmpeg. Generated audio (voices, effects, music, transcripts) comes from the
Drama API through one interface (:mod:`creation.post.audio_service`); no
provider key is ever on this laptop. Nothing in this package holds a server
prompt or template; it reads the take's facts (``GET /v1/jobs/{take_job}/take-facts``)
and the saved spine.
"""
