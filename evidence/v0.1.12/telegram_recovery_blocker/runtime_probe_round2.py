
import asyncio,aiohttp,ssl,sys,os,json,pathlib,socket,datetime,hashlib
async def main():
 processes=[]
 for proc in pathlib.Path('/proc').iterdir():
  if not proc.name.isdigit():continue
  try:
   argv=(proc/'cmdline').read_bytes().split(b'\0')
   if not any(b'process_runner' in a for a in argv):continue
   env=dict(x.split(b'=',1) for x in (proc/'environ').read_bytes().split(b'\0') if b'=' in x)
   processes.append({'pid':proc.name,'executable':os.readlink(proc/'exe'),'runner':next(a.decode(errors='replace') for a in argv if b'process_runner' in a),'env_presence':{k:bool(env.get(k.encode())) for k in ['HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy','SSL_CERT_FILE','SSL_CERT_DIR']},'ca_matches_probe':all(env.get(k.encode())==(os.environ[k].encode() if k in os.environ else None) for k in ['SSL_CERT_FILE','SSL_CERT_DIR'])})
  except (OSError,ValueError):pass
 print(json.dumps({'at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'runner_processes':processes,'default_ca_sha256':hashlib.sha256(pathlib.Path(ssl.get_default_verify_paths().cafile).read_bytes()).hexdigest()}),flush=True)
 async def fetch(url,ipv4=False):
  start=asyncio.get_running_loop().time()
  try:
   connector=aiohttp.TCPConnector(family=socket.AF_INET) if ipv4 else None
   async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=25),connector=connector) as session:
    async with session.get(url) as resp:
     body=await resp.text();print(json.dumps({'url':url,'ipv4_only':ipv4,'status':resp.status,'seconds':round(asyncio.get_running_loop().time()-start,3),'body':body}),flush=True)
  except Exception as e:print(json.dumps({'url':url,'ipv4_only':ipv4,'error_type':type(e).__name__,'error':str(e),'seconds':round(asyncio.get_running_loop().time()-start,3)}),flush=True)
 await asyncio.gather(fetch('https://t.me/s/codexresetalerts',True),fetch('https://t.me/codexresetalerts/70?embed=1'),fetch('https://codex-reset.com/api/push/notification'))
asyncio.run(main())
