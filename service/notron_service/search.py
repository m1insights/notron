"""Search results are bounded untrusted text; URLs are never fetched."""
def optional_text(data,key,limit):
    value=data.get(key)
    if value is None: return ''
    if not isinstance(value,str) or len(value)>limit: raise ValueError('invalid_response')
    return value

def validate_search(data,limit):
    if not isinstance(data,dict): raise ValueError('invalid_response')
    answer=optional_text(data,'answer',65536)
    results=data.get('results')
    if not isinstance(results,list) or len(results)>limit: raise ValueError('invalid_response')
    safe=[]
    for r in results:
        if not isinstance(r,dict): raise ValueError('invalid_response')
        row={k:optional_text(r,k,32768) for k in ('title','url','content')}
        safe.append(row)
    return {'answer':answer,'results':safe}
