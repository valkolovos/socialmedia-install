#!/bin/bash
cd socialmedia-frontend
echo "Building with project name $1 and app host $2"
PROJECT=$1 APP_HOST=$2 gulp build
