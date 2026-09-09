"""Run from an approved scheduler; prints fixed counters, never provider payloads.

    python -m notron_service.billing_repair --limit 100

Environment and billing policy are identical to the web service. No migration or
provisioning occurs. Run at least once per minute after staging qualification.
"""
import argparse
import json
from .app import create_default_app


def main(argv=None):
    parser=argparse.ArgumentParser(description='Replay billing inbox and reconcile authoritative Stripe state')
    parser.add_argument('--limit',type=int,default=100)
    args=parser.parse_args(argv)
    if not 1<=args.limit<=1000:
        parser.error('limit must be between 1 and 1000')
    try:
        billing=create_default_app().state.services.billing
        if billing is None:
            print(json.dumps({'code':'billing_unavailable'}))
            return 1
        result=billing.repair(args.limit)
        print(json.dumps(result,sort_keys=True))
        return 1 if result['failed'] else 0
    except Exception:
        print(json.dumps({'code':'billing_repair_failed'}))
        return 1


if __name__=='__main__':
    raise SystemExit(main())
