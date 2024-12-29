#!/usr/bin/env python3
from flask import Flask, send_from_directory
import os

app = Flask(__name__)

@app.after_request
def add_security_headers(response):
    # Required for SharedArrayBuffer
    response.headers['Cross-Origin-Opener-Policy'] = 'same-origin'
    response.headers['Cross-Origin-Embedder-Policy'] = 'require-corp'
    return response

@app.route('/')
def serve_index():
    return send_from_directory('.', 'index.html')

@app.route('/<path:path>')
def serve_file(path):
    return send_from_directory('.', path)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8000))
    app.run(host='localhost', port=port, debug=True) 