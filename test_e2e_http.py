import requests

BASE = 'http://127.0.0.1:8000'

def check(name, cond, details=''):
    status = 'PASS' if cond else 'FAIL'
    print(f'[{status}] {name} {details}')
    assert cond, f'{name} failed: {details}'

print('--- TESTING STATIC FILES & NO SHADOWING ---')
r = requests.get(f'{BASE}/')
check('GET / status', r.status_code == 200, f'Status: {r.status_code}')
check('GET / content-type', 'text/html' in r.headers.get('content-type', ''), r.headers.get('content-type'))
check('GET / content', 'SPHEREx Sky Viewer' in r.text)

r = requests.get(f'{BASE}/static/styles.css')
check('GET /static/styles.css status', r.status_code == 200)

r = requests.get(f'{BASE}/static/app.js')
check('GET /static/app.js status', r.status_code == 200)

r = requests.get(f'{BASE}/api/bands')
check('GET /api/bands not shadowed', r.status_code == 200 and 'bands' in r.json())

print('\n--- TESTING RESOLVE ---')
r = requests.get(f'{BASE}/api/resolve?name=M101')
check('GET /api/resolve?name=M101', r.status_code == 200 and 'ra' in r.json(), r.text)

r = requests.get(f'{BASE}/api/resolve?name=NONEXISTENT_TARGET_XYZ987')
check('GET /api/resolve invalid target', r.status_code in (400, 404), f'Status: {r.status_code}, {r.text}')

print('\n--- TESTING OUT OF RANGE INPUTS ---')
r = requests.get(f'{BASE}/api/observations?ra=-10&dec=0&band=SPHEREx-D2&size=0.1')
check('ra < 0 rejected', r.status_code in (400, 422), f'Status: {r.status_code}')

r = requests.get(f'{BASE}/api/observations?ra=400&dec=0&band=SPHEREx-D2&size=0.1')
check('ra >= 360 rejected', r.status_code in (400, 422), f'Status: {r.status_code}')

r = requests.get(f'{BASE}/api/observations?ra=0&dec=95&band=SPHEREx-D2&size=0.1')
check('dec > 90 rejected', r.status_code in (400, 422), f'Status: {r.status_code}')

r = requests.get(f'{BASE}/api/observations?ra=0&dec=0&band=SPHEREx-D2&size=0.5')
check('size > 0.2 rejected', r.status_code in (400, 422), f'Status: {r.status_code}')

r = requests.get(f'{BASE}/api/observations?ra=0&dec=0&band=SPHEREx-INVALID&size=0.1')
check('invalid band rejected', r.status_code in (400, 422), f'Status: {r.status_code}')

print('\n--- TESTING SAMPLE MODE ---')
r = requests.get(f'{BASE}/api/sample')
check('GET /api/sample status', r.status_code == 200)
data = r.json()
obs_count = len(data.get('observations', []))
check('GET /api/sample has observations', obs_count > 0, f'Count: {obs_count}')
check('GET /api/sample sample_mode flag', data.get('sample_mode') is True)

print('\n--- TESTING VALID TARGET OBSERVATIONS (M101, D2) ---')
r = requests.get(f'{BASE}/api/observations?ra=210.80227&dec=54.34895&band=SPHEREx-D2&size=0.1')
check('GET /api/observations M101 status', r.status_code == 200)
obs_data = r.json()
obs = obs_data.get('observations', [])
check('GET /api/observations count > 0', len(obs) > 0, f'Count: {len(obs)}')
releases = set(o.get('release') for o in obs)
print(f'Releases present: {releases}')

print('\nALL HTTP AND API ENDPOINT CHECKS PASSED!')
