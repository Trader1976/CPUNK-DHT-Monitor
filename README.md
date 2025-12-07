
<img width="890" height="876" alt="Pasted image (2)" src="https://github.com/user-attachments/assets/caf098f9-42e3-46a6-b976-41f8ba770c6f" />

# CPUNK DHT Monitor

A lightweight, real-time monitoring dashboard for CPUNK DHT bootstrap nodes and DNA-Messenger infrastructure.  
Built with **FastAPI**, **Chart.js**, and a **background packet capture engine** using `tshark`.

![Features](https://img.shields.io/badge/Features-RealTime%20Metrics-blue)
![Python](https://img.shields.io/badge/Python-3.12-green)

---

## 🚀 Features

### **DHT Traffic Monitoring**
- Unique peer count (UDP sources)
- Bytes/packets per capture window
- Top talkers (IP/bytes/packets)
- Historical graphing (peers + traffic)

### **System Metrics**
- CPU usage %
- Memory usage %
- Disk usage (root filesystem)
- Trendlines (sparklines) for CPU/RAM/Disk


### **Persistence**
- SQLite database for long-term storage
- `/db_stats` endpoint for introspection

### **Web Dashboard**
- Served as static HTML
- FastAPI JSON API backend
- Chart.js visualizations
- HTTP Basic Auth protection

---

## 📦 Installation

### Clone the repository

```bash
git clone https://github.com/Trader1976/cpunk-dht-monitor.git
cd cpunk-dht-monitor


Create virtual environment
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

Run
python dht_fastapi_app.py


Dashboard:

http://SERVER_IP:8080/

🔧 Systemd Service (optional)
[Unit]
Description=CPUNK DHT Monitor
After=network.target

[Service]
WorkingDirectory=/opt/cpunk-dht-monitor
ExecStart=/opt/cpunk-dht-monitor/venv/bin/python dht_fastapi_app.py
Restart=always

[Install]
WantedBy=multi-user.target

🗂 Project Structure
cpunk-dht-monitor/
  ├── dht_core.py
  ├── dht_fastapi_app.py
  ├── static/index.html
  ├── requirements.txt
  ├── README.md
  └── .gitignore

License

MIT – free to use, fork, modify.<img width="1120" height="958" alt="Pasted image" src="https://github.com/user-attachments/assets/36bbdff1-9cc9-43bc-8fdd-5f85f0424b01" />

