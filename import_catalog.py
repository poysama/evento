"""Review explicit official Event-card HTML and conditionally apply catalog additions."""
import argparse
import copy
import hashlib
import html
import json
import re
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


def validate_source(url):
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or parsed.netloc != 'www.onepiece-cardgame.com' or parsed.path != '/cardlist/':
        raise ValueError('Source must be an explicit Japanese official HTTPS cardlist URL')
    return url


def clean(value):
    return html.unescape(re.sub('<[^>]+>', '', value)).strip()


def parse_official(document, source):
    validate_source(source)
    rows = []
    for match in re.finditer(r'<dl\s+class="modalCol"\s+id="([A-Z0-9-]+(?:_p\d+)?)"[^>]*>(.*?)(?=<dl\s+class="modalCol"|\Z)', document, re.S):
        official_id, block = match.groups()
        info = re.search(r'<div class="infoCol">(.*?)</div>',block,re.S)
        spans = re.findall(r'<span[^>]*>(.*?)</span>', info[1],re.S) if info else []
        if len(spans) < 3 or clean(spans[2]) != 'EVENT':
            continue
        name = re.search(r'<div class="cardName">(.*?)</div>',block,re.S)
        product = re.search(r'<div class="getInfo"><h3>.*?</h3>(.*?)(?:<div|</div>)',block,re.S)
        color = re.search(r'<div class="color"><h3>.*?</h3>(.*?)</div>',block,re.S)
        if not name or not product:
            raise ValueError('Official Event record is missing required source fields')
        rows.append({'num':clean(spans[0]),'officialId':official_id,'name':clean(name[1]),'rar':clean(spans[1]),'product':clean(product[1]),'color':clean(color[1]) if color else '', 'sourceUrl':source})
    if not rows:
        raise ValueError('No official Event records found; refusing an empty or changed feed')
    return rows


def propose(catalog, rows):
    result = copy.deepcopy(catalog)
    changes, review = [], []
    cards = result.setdefault('cards',[])
    for row in rows:
        matches = [c for c in cards if c['num'] == row['num']]
        if len(matches) > 1:
            review.append({'reason':'Duplicate catalog number','record':row})
            continue
        parallel = row['officialId'] != row['num']
        if parallel:
            known = matches and any(v.get('officialId') == row['officialId'] for v in matches[0].get('variants',[]))
            if not known:
                review.append({'reason':'Parallel needs explicit identity/kind mapping; print suffix does not identify Manga','record':row})
                continue
        if not matches:
            set_code = re.sub(r'^(OP|ST|EB|PRB)(\d+)-.*',r'\1-\2',row['num'])
            colors = {'赤':'Red','緑':'Green','青':'Blue','紫':'Purple','黒':'Black','黄':'Yellow'}
            cards.append({'num':row['num'],'name':row['name'],'jp':row['name'],'rar':row['rar'],'set':set_code,'col':colors.get(row['color'],row['color']),'slot':None,'foil':False,'status':'released','sourceUrl':row['sourceUrl']})
            matches = [cards[-1]]
            changes.append({'action':'add unplaced base','num':row['num']})
        aliases = matches[0].setdefault('officialPrints',[])
        if row not in aliases:
            aliases.append(copy.deepcopy(row))
            changes.append({'action':'record official alias','num':row['num'],'officialId':row['officialId']})
    return {'catalog':result,'changes':changes,'review':review}


def read_api(endpoint, cookie=None):
    headers = {'Cookie':cookie} if cookie else {}
    with urlopen(Request(endpoint,headers=headers),timeout=30) as response:
        etag = response.headers.get('ETag')
        if not etag:
            raise ValueError('Catalog endpoint did not supply an ETag')
        return json.load(response), etag


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command',required=True)
    preview = sub.add_parser('preview')
    preview.add_argument('--source-url',required=True)
    preview.add_argument('--html',type=Path,help='Saved official HTML; otherwise fetch the explicit URL')
    preview.add_argument('--endpoint',default='http://127.0.0.1:8765/api/catalog')
    preview.add_argument('--cookie',help='Authenticated session cookie, when needed')
    preview.add_argument('--out',type=Path,required=True)
    apply = sub.add_parser('apply')
    apply.add_argument('proposal',type=Path)
    apply.add_argument('--cookie')
    args = parser.parse_args()
    if args.command == 'preview':
        validate_source(args.source_url)
        if args.html:
            document = args.html.read_text(encoding='utf-8')
        else:
            with urlopen(args.source_url,timeout=30) as response:
                validate_source(response.url)
                document = response.read().decode('utf-8')
        catalog, etag = read_api(args.endpoint,args.cookie)
        proposal = propose(catalog,parse_official(document,args.source_url))
        proposal.update(endpoint=args.endpoint,etag=etag,sourceSha256=hashlib.sha256(document.encode()).hexdigest())
        args.out.write_text(json.dumps(proposal,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(json.dumps({k:proposal[k] for k in ('changes','review')},ensure_ascii=False,indent=2))
        print('Review the proposal file. Apply sends only listed catalog changes; review items are excluded.')
    else:
        proposal = json.loads(args.proposal.read_text(encoding='utf-8'))
        headers = {'Content-Type':'application/json','If-Match':proposal['etag']}
        if args.cookie:
            headers['Cookie'] = args.cookie
        request = Request(proposal['endpoint'],data=json.dumps(proposal['catalog']).encode(),headers=headers,method='PUT')
        with urlopen(request,timeout=30) as response:
            print('Applied catalog with revision',response.headers.get('ETag',''))

if __name__ == '__main__':
    main()
