#!/usr/bin/python
import requests
import sys

def main():
    print('Draining idle containers from the pool ...')
    sys.stdout.flush()
    resp = requests.delete('http://127.0.0.1:10000/api/pool')
    resp.raise_for_status()
    n = resp.json()['drained']
    print('Drained and replaced %d containers' % n)

if __name__ == '__main__':
    main()