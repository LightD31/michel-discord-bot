import asyncio
import aiohttp
import json

URL = "https://zunivers-api.zerator.com/public/hardcore/season/current"

async def main():
    print(f"Fetching {URL}...")
    headers = {"X-ZUnivers-RuleSetType": "HARDCORE"}
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(URL, headers=headers) as response:
                print(f"Status Code: {response.status}")
                
                if response.status == 200:
                    try:
                        data = await response.json()
                        print("Response JSON:")
                        print(json.dumps(data, indent=4))
                    except Exception as e:
                        print(f"Error parsing JSON: {e}")
                        text = await response.text()
                        print(f"Response Text: {text}")
                else:
                    text = await response.text()
                    print(f"Response Text: {text}")
        except Exception as e:
            print(f"Request failed: {e}")

if __name__ == "__main__":
    asyncio.run(main())
