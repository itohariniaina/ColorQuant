from flask import Flask, request, jsonify, render_template
from app.worker import process_image_task
import os
import uuid
import redis  #
import imghdr
from werkzeug.middleware.proxy_fix import ProxyFix

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = '/tmp/uploads'

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)



if os.getenv('AZURE_DEPLOYMENT') == 'True':
    print(" Production (Azure) : ProxyFix activé")
    
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
else:
    print("Local : ProxyFix désactivé")

r = redis.Redis(host='redis', port=6379, db=1, decode_responses=True)

MAX_FREE_REQUESTS = 5
QUOTA_PERIOD_SECONDS = 86400 

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/optimize', methods=['POST'])
def optimize():
    client_ip = request.remote_addr
    
    redis_key = f"quota:{client_ip}"
    
   
    current_usage = r.get(redis_key)
    
    if current_usage is None:
        current_usage = 0
    else:
        current_usage = int(current_usage)
    
    if current_usage >= MAX_FREE_REQUESTS:
        return jsonify({
            "error": "Quota gratuit journalier dépassé ! Passez à la version Pro.",
            "remaining": 0
        }), 429
    
    
    if 'image' not in request.files:
        return jsonify({"error": "Aucune image envoyée"}), 400
        
    file = request.files['image']
    header = file.read(512)
    file.seek(0) 
    
    format_detecte = imghdr.what(None, header)
    if format_detecte not in ['jpeg', 'png', 'gif']:
        return jsonify({"error": "Format invalide. Seuls JPG et PNG sont acceptés."}), 400
    n_colors = int(request.form.get('colors', 8))
    
  
    ext = file.filename.split('.')[-1]
    filename = f"{uuid.uuid4()}.{ext}"
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(filepath)
    
    new_usage = r.incr(redis_key)
    
    if new_usage == 1:
        r.expire(redis_key, QUOTA_PERIOD_SECONDS)
    
    task = process_image_task.delay(filepath, n_colors)
    
    return jsonify({
        "message": "Traitement démarré",
        "task_id": task.id,
        "quota_used": f"{new_usage}/{MAX_FREE_REQUESTS}"
    }), 202

@app.route('/api/status/<task_id>')
def status(task_id):
    task = process_image_task.AsyncResult(task_id)
    
    if task.state == 'PENDING':
        return jsonify({"state": "PROCESSING"}), 200
    elif task.state == 'SUCCESS':
        return jsonify(task.result), 200
    elif task.state == 'FAILURE':
        return jsonify({"state": "FAILURE", "error": str(task.info)}), 500
    
    return jsonify({"state": task.state}), 200

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)