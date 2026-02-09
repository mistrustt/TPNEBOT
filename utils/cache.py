import aiohttp
import time

JUICEWRLD_API = "https://juicewrldapi.com"

# This class is to prevent the songs being recached everytime the music cog is reloaded.
class Cache:
    songs = []
    session = None
    
    @staticmethod
    async def get_songs():
        if not Cache.songs:
            await Cache.fetch_songs()
        return Cache.songs

    @staticmethod
    async def init():
        Cache.session = aiohttp.ClientSession() 
        await Cache.get_songs()

    @staticmethod
    async def fetch_songs():
        url = JUICEWRLD_API + '/juicewrld/songs/'
        songs = []

        while url:
            async with Cache.session.get(url) as response:
                if response.status != 200:
                    return response.status
                    
                data = await response.json()
                songs.extend(data.get('results', []))
                url = data.get('next')    

        Cache.songs = songs
        return 200