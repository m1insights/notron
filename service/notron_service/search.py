"""Search results are bounded untrusted text; URLs are never fetched."""
def validate_search(data,limit):
    answer=data.get('answer') or ''
    if not isinstance(answer,str) or len(answer)>65536: raise ValueError('invalid_response')
    results=data.get('results')
    if not isinstance(results,list) or len(results)>limit: raise ValueError('invalid_response')
    safe=[]
    for r in results:
        if not isinstance(r,dict): raise ValueError('invalid_response')
        row={k:r.get(k) or '' for k in ('title','url','content')}
        if any(not isinstance(v,str) or len(v)>32768 for v in row.values()): raise ValueError('invalid_response')
        safe.append(row)
    return {'answer':answer,'results':safe}
