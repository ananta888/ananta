# Fügen Sie diesen Code in Ihre Controller-Flask-App ein

@app.route('/health')
def health_check():
    """Health-Endpoint für den Controller."""
    return jsonify({"status": "ok"})
