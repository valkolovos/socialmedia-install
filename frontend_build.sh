#!/bin/bash
cd socialmedia-frontend
echo "Building with project name $1"
PROJECT=$1 gulp build
