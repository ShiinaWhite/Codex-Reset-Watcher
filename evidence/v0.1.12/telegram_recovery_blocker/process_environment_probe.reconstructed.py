# Reconstructed from the recorded tool-call source when packaging evidence.
# Not an original on-disk capture and not rerun. Retains the NUL delimiter
# used by the original recorded tool-call source.
import os,pathlib,json,ssl
rows=[]
for proc in pathlib.Path('/proc').iterdir():
 if not proc.name.isdigit():continue
 try:
  exe=os.readlink(proc/'exe')
  if 'python' not in exe.lower():continue
  argv=(proc/'cmdline').read_bytes().split(b'\0')
  env=dict(x.split(b'=',1) for x in (proc/'environ').read_bytes().split(b'\0') if b'=' in x)
  rows.append({'pid':proc.name,'is_probe':int(proc.name)==os.getpid(),'executable':exe,'script_paths':[a.decode(errors='replace') for a in argv if a.endswith(b'.py')],'same_network_namespace':os.readlink(proc/'ns/net')==os.readlink('/proc/self/ns/net'),'env_presence':{k:bool(env.get(k.encode())) for k in ['HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy','SSL_CERT_FILE','SSL_CERT_DIR']},'ca_matches_probe':all(env.get(k.encode())==(os.environ[k].encode() if k in os.environ else None) for k in ['SSL_CERT_FILE','SSL_CERT_DIR'])})
 except (OSError,ValueError):pass
print(json.dumps(rows))
