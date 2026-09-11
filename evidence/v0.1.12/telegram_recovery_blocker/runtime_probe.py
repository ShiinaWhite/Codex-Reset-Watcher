
import asyncio,aiohttp,ssl,sys,os,json,pathlib,hashlib,datetime
async def main():
 meta={'at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'python':sys.version,'executable':sys.executable,'aiohttp':aiohttp.__version__,'openssl':ssl.OPENSSL_VERSION,'ca':ssl.get_default_verify_paths()._asdict(),'proxy_env_present':{k:bool(os.environ.get(k)) for k in ['HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','NO_PROXY']},'trust_env':False}
 plugin=pathlib.Path('/MaiMBot/plugins/codex-reset.watcher/plugin.py')
 meta['plugin_sha256']=hashlib.sha256(plugin.read_bytes()).hexdigest()
 meta['version']=json.loads(plugin.with_name('_manifest.json').read_text()).get('version')
 print(json.dumps({'metadata':meta}),flush=True)
 async def fetch(url):
  start=asyncio.get_running_loop().time()
  try:
   async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=12)) as session:
    async with session.get(url) as resp:
     body=await resp.text();print(json.dumps({'url':url,'status':resp.status,'final_url':str(resp.url),'seconds':round(asyncio.get_running_loop().time()-start,3),'body':body}),flush=True)
  except Exception as e: print(json.dumps({'url':url,'error_type':type(e).__name__,'error':str(e),'seconds':round(asyncio.get_running_loop().time()-start,3)}),flush=True)
 await asyncio.gather(fetch('https://t.me/s/codexresetalerts'),fetch('https://codex-reset.com/api/push/notification'))
asyncio.run(main())
