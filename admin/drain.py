#!/usr/bin/python
import requests

def main():
    resp = requests.delete('http://127.0.0.1:10000/api/pool')
    resp.raise_for_status()
    print(resp.status_code)

if __name__ == '__main__':
    main()