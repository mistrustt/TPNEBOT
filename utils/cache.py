import aiohttp
import time

JUICEWRLD_API = "https://juicewrldapi.com"
RECACHE_DELAY = 60*60  # 1 hour in seconds

# This class is to prevent the songs being recached everytime the music cog is reloaded.
class Cache:
    songs = []
    session = aiohttp.ClientSession()
    last_cache_time = None
    
    @staticmethod
    async def get_songs():
        if not Cache.songs or (Cache.last_cache_time and time.time() - Cache.last_cache_time > RECACHE_DELAY):
            await Cache.fetch_songs()
            Cache.last_cache_time = time.time()
        return Cache.songs

    @staticmethod
    async def init():
        await Cache.fetch_songs()

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