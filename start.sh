#!/bin/bash
gunicorn main:app --bind 0.0.0.0:8080 --workers 1 --threads 8 --timeout 0
