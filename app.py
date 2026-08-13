from flask import Flask, jsonify, render_template
from db import latest_scan, get_history
import os

app = Flask(__name__, template_folder="./templates")


@app.route('/api/current')
def api_current():
    s = latest_scan()
    if not s:
        return jsonify({"ok": False, "message": "no data"}), 404
    return jsonify({"ok": True, "scan": s})


@app.route('/api/history')
def api_history():
    hist = get_history(500)
    return jsonify({"ok": True, "history": hist})


@app.route('/')
def index():
    return render_template('index.html')


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
