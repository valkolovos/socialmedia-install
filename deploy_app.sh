#!/bin/bash
cd socialmedia
gcloud app deploy --quiet
gcloud app deploy cron.yaml --quiet
