import pathlib,json,gzip,tarfile,io,hashlib,sys
root=pathlib.Path(sys.argv[1]);arch=sys.argv[2]
urls={}
for path in root.glob('*urls.txt'):
    for line in path.read_text().splitlines():
        if line.startswith('https:'): urls[line.rsplit('/',1)[-1]]=line
artifacts=[];installed=0
for path in sorted((root/'packages'/arch).glob('*.apk')):
    data=path.read_bytes()
    with tarfile.open(fileobj=io.BytesIO(gzip.decompress(data)),mode='r:',ignore_zeros=True) as tar:
        members=tar.getmembers()
        installed+=sum(m.size for m in members)
        info=tar.extractfile('.PKGINFO').read().decode()
        props=dict(line.split(' = ',1) for line in info.splitlines() if ' = ' in line)
    url=urls.get(path.name)
    if not url: raise RuntimeError('Missing source URL: '+path.name)
    artifacts.append(dict(name=path.name,url=url,sha256=hashlib.sha256(data).hexdigest(),size_bytes=len(data),format='apk',license=props['license'],source='https://gitlab.alpinelinux.org/alpine/aports/-/tree/3.23-stable/'+url.split('/')[-3]+'/'+props['origin']))
path=root/'html5-v20.tar.gz';data=path.read_bytes()
with tarfile.open(path) as tar: installed+=sum(m.size for m in tar.getmembers())
artifacts.append(dict(name=path.name,url='https://github.com/Xpra-org/xpra-html5/archive/refs/tags/v20.tar.gz',sha256=hashlib.sha256(data).hexdigest(),size_bytes=len(data),format='html5',license='MPL-2.0',source='https://github.com/Xpra-org/xpra-html5/tree/v20'))
pkg=dict(id='alpine-3.23-xpra-6.2.2-'+arch,architecture=arch,size_bytes=sum(a['size_bytes'] for a in artifacts),installed_bytes=installed+16*1024*1024,artifacts=artifacts)
(root/(arch+'-catalog.json')).write_text(json.dumps(pkg,indent=2)+'\n')
print(arch,len(artifacts),pkg['size_bytes'],pkg['installed_bytes'])
