#!/usr/bin/env python3
"""Entry point for running the Aplus Player web UI."""
import os
import sys
import argparse

# Add the current directory to the path so we can import app/player, etc.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from webapp import create_app

def main():
    """Run the Flask web application."""
    parser = argparse.ArgumentParser(description="Aplus Player Web UI")
    parser.add_argument('--host', default='127.0.0.1',
                       help='Host to bind to (default: 127.0.0.1)')
    parser.add_argument('--port', type=int, default=5000,
                       help='Port to run on (default: 5000)')
    parser.add_argument('--debug', action='store_true',
                       help='Run in debug mode')

    args = parser.parse_args()

    app = create_app()

    print("Starting Aplus Player Web UI...")
    print(f"Access the web interface at: http://{args.host}:{args.port}")
    print("Press Ctrl+C to stop the server")

    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
