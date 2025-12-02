import json
from flask import Flask, jsonify
from scraper import collect_pis_data

app = Flask(__name__)

# Read options from /data/options.json (standard HA add-on path)
with open("/data/options.json", "r", encoding="utf-8") as f:
    opts = json.load(f)

USERNAME = opts.get("username")
PASSWORD = opts.get("password")

if not USERNAME or not PASSWORD:
    raise RuntimeError("username/password not set in add-on options.")


@app.route("/data", methods=["GET"])
def get_data():
    try:
        data = collect_pis_data(USERNAME, PASSWORD)
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
