import requests

BASE = "http://localhost:8000"
tok = requests.post(f"{BASE}/auth/login",
                    json={"username": "admin", "password": "admin1234"},
                    timeout=10).json()["token"]
r = requests.get(f"{BASE}/admin/dashboard",
                 headers={"Authorization": f"Bearer {tok}"}, timeout=10)
print(r.json())

r2 = requests.get(f"{BASE}/auth/companies", headers={"Authorization": f"Bearer {tok}"}, timeout=10)
print("companies:", r2.status_code, r2.json())