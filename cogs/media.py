from discord.ext import commands
from discord.ext.commands import Context
import discord
from discord.ui import View, Button
import json
import aiohttp
import re
import asyncio
from io import BytesIO
import os
import aiofiles
import gc
import tempfile
import shutil
import time
import html
from typing import Optional


class MediaPaginationView(View):
    def __init__(self, context: Context, picker_items: list, original_url: str, testing_cog):
        super().__init__(timeout=300)                    
        self.context = context
        self.picker_items = picker_items
        self.original_url = original_url
        self.testing_cog = testing_cog
        self.current_index = 0
        self.message = None
        
                              
        self.update_buttons()
    
    def update_buttons(self):
        """Update button states based on current position"""
        self.previous_button.disabled = self.current_index == 0
        self.next_button.disabled = self.current_index >= len(self.picker_items) - 1
    
    @discord.ui.button(label='Previous', style=discord.ButtonStyle.primary, disabled=True)
    async def previous_button(self, interaction: discord.Interaction, button: Button):
        if self.current_index > 0:
            self.current_index -= 1
            await self.update_media(interaction)
    
    @discord.ui.button(label='Next', style=discord.ButtonStyle.primary)
    async def next_button(self, interaction: discord.Interaction, button: Button):
        if self.current_index < len(self.picker_items) - 1:
            self.current_index += 1
            await self.update_media(interaction)
    
    async def update_media(self, interaction: discord.Interaction):
        """Update the displayed media to the current index"""
        await interaction.response.defer()
        
        current_item = self.picker_items[self.current_index]
        item_url = current_item.get("url")
        item_type = current_item.get("type", "media")
        
        if item_url:
            try:
                                                                         
                embed = self.testing_cog._styled_embed(
                    title=f"Instagram Post • {self.current_index + 1}/{len(self.picker_items)}",
                    color=0x2B2D31
                )
                embed.set_image(url=item_url)
                embed.add_field(
                    name="Post",
                    value=f"[View original]({self.original_url})",
                    inline=False
                )
                
                                
                self.update_buttons()
                
                                                                         
                await interaction.edit_original_response(
                    embed=embed,
                    attachments=[],                                  
                    view=self
                )
                    
            except Exception as e:
                print(f"Error updating media: {e}")
                embed = self.testing_cog._styled_embed(
                    title="Error",
                    description=f"An error occurred while loading image {self.current_index + 1}",
                    color=0xE02B2B
                )
                self.update_buttons()
                await interaction.edit_original_response(embed=embed, view=self)
    
    async def on_timeout(self):
        """Called when the view times out"""
                             
        for item in self.children:
            item.disabled = True
        
        if self.message:
            try:
                await self.message.edit(view=self)
            except:
                pass                                   


                                                          
class Media(commands.Cog, name="media"):
    @commands.Cog.listener()
    async def on_message(self, message):
                                   
        if message.author.bot:
            return

                                                                                      
        if message.content.lower().startswith('remorse '):
                                                                                             
            arg_string = message.content[len('remorse '):].strip()
                                                                              
            ctx = await self.bot.get_context(message)
                                                                                                     
            await ctx.invoke(self.remorse, url=arg_string)
    def __init__(self, bot) -> None:
        self.bot = bot
        
                                                                       
        self._session_connector = aiohttp.TCPConnector(
            limit=100,
            ttl_dns_cache=300,
            force_close=False,
            enable_cleanup_closed=True
        )
        self._session = None
                                                                                             
        self._download_connector = aiohttp.TCPConnector(
            limit=0,                                             
            ttl_dns_cache=600,
            force_close=False,
            enable_cleanup_closed=True
        )
        self._download_session = None
        
                                                                    
        self.cobalt_apis = [
                                           

            {
                "url": "https://cobalt-api.meowing.de",
                "key": "1f6af052-e9f0-433a-8945-5185b9028109",
                "name": "meowing"
            },

                               

                        {
                "url": "https://cobalt-backend.canine.tools",
                "key": "3d8adb1b-0459-4417-a2f9-0e163b9274f4",
                "name": "canine"
            },
            
                                                             
            {
                "url": "https://instagram.embedez.com",
                "key": None,
                "name": "embedez",
                "instagram_only": True
            }
                                 
               
                                                                                   
                                                                
                                           
                
               
                                                     
                                                                
                                             
                
               
                                                      
                                                                
                                              
               
        ]
        self.media_folder = "media"
        
                                                 
        if not os.path.exists(self.media_folder):
            os.makedirs(self.media_folder)
    
    async def get_session(self):
        """Get or create a persistent aiohttp session for speed"""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(connector=self._session_connector)
        return self._session

    async def get_download_session(self):
        """Get or create the high-throughput session used for large media downloads."""
        if self._download_session is None or self._download_session.closed:
            self._download_session = aiohttp.ClientSession(connector=self._download_connector)
        return self._download_session

    def _styled_embed(
        self,
        *,
        title: str | None = None,
        description: str | None = None,
        color: int | None = None,
        footer_text: str | None = "remorse",
        show_footer: bool = True,
    ) -> discord.Embed:
        """Return a unified embed style similar to userinfo.py."""
        embed = discord.Embed(
            title=title,
            description=description,
            color=color if color is not None else 0x2B2D31,
        )
        if show_footer:
            footer_value = footer_text or "remorse"
            icon_url = None
            try:
                if self.bot.user and self.bot.user.avatar:
                    icon_url = self.bot.user.avatar.url
            except Exception:
                icon_url = None
            embed.set_footer(text=footer_value, icon_url=icon_url)
        return embed
    
    def cog_unload(self):
        """Cleanup session on cog unload"""
        if self._session and not self._session.closed:
            asyncio.create_task(self._session.close())
        if self._download_session and not self._download_session.closed:
            asyncio.create_task(self._download_session.close())

    @commands.hybrid_command(
        name="remorse",
        description="Pulls media from a link.",
        aliases=["zak"]
    )
    @commands.cooldown(1, 20, commands.BucketType.user)
    async def remorse(self, context: Context, *, url: str) -> None:
        """
        Syntax: remorse <url> [quality] [format]
        """
        url = url.strip()
        quality = None
        file_format = None
                                                              
                            
        allowed_formats = {"mp4", "webm", "mp3", "m4a", "ogg", "opus", "wav", "flac"}
                                                    
        match = re.search(r"\s(\d{3,4}p?)?(?:\s)?(mp4|webm|mp3|m4a|ogg|opus|wav|flac)?$", url, re.IGNORECASE)
        if match:
                             
            if match.group(1):
                quality = match.group(1).lower()
                if quality.isdigit():
                    quality = f"{quality}p"
            if match.group(2):
                file_format = match.group(2).lower()
                                          
            url = url[:match.start()].strip()
                                                                    
        context.remorse_requested_quality = quality
                         
        if file_format and file_format not in allowed_formats:
            await context.send(f"Invalid format: `{file_format}`. Allowed: {', '.join(sorted(allowed_formats))}")
            return
                                                                
        max_discord_size = 10 * 1024 * 1024
                             
        url_pattern = re.compile(
            r'^https?://'                       
            r'(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+[A-Z]{2,6}\.?|'             
            r'localhost|'                
            r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})'            
            r'(?::\d+)?'                 
            r'(?:/?|[/?]\S+)$', re.IGNORECASE)
        if not url_pattern.match(url):
            return                               
        
                                                                                     
        await context.defer()
        await self._try_apis_with_quality(context, url, quality, file_format)

    async def _try_apis_with_quality(self, context: Context, url: str, quality: str = None, file_format: str = None) -> None:
        """Try each API endpoint using appropriate download mode based on requested format."""
        last_error = None
        status_embed = self._styled_embed(
            title="Downloading...",
            description="Initializing download..."
        )
        status_message = await self._send_response(context, embed=status_embed, return_message=True)
        context.status_message = status_message
        
                                                                                                
        primary_api = self.cobalt_apis[0]                       
        
                                                                                                              
        should_skip_primary = False
        if primary_api.get("instagram_only") and "instagram.com" not in url:
            should_skip_primary = True
        elif "instagram.com" in url and not primary_api.get("instagram_only"):
                                                              
            has_instagram_api = any(api.get("instagram_only") for api in self.cobalt_apis)
            if has_instagram_api:
                should_skip_primary = True
        
        if not should_skip_primary:
            print(f"Exhaustively trying primary API: {primary_api['name']}")
            await self._update_status(context, "Downloading file...", f"trying all payloads on primary api: {primary_api['name']}")
            
                                                      
            if primary_api.get("instagram_only") and "instagram.com" in url:
                try:
                    result = await self._try_instagram_embedez_api(context, url, primary_api, forced_quality=quality, forced_format=file_format)
                    if result:
                        try:
                            if hasattr(context, 'message') and context.message:
                                await context.message.delete()
                        except Exception as e:
                            print(f"Failed to delete original message: {e}")
                        try:
                            if hasattr(context, 'status_message') and context.status_message:
                                await context.status_message.delete()
                        except Exception as e:
                            print(f"Failed to delete status message: {e}")
                        return
                except Exception as e:
                    print(f"Instagram embedez API {primary_api['name']} failed: {str(e)}")
                    last_error = e
            else:
                                                                           
                headers = {
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                }
                if primary_api.get("key"):
                    headers["Authorization"] = f"Api-Key {primary_api['key']}"
                try:
                    result = await self._try_download_with_exhaustive_payloads(context, url, headers, primary_api, send_status=False, forced_quality=quality, forced_format=file_format)
                    if result:
                        try:
                            if hasattr(context, 'message') and context.message:
                                await context.message.delete()
                        except Exception as e:
                            print(f"Failed to delete original message: {e}")
                        try:
                            if hasattr(context, 'status_message') and context.status_message:
                                await context.status_message.delete()
                        except Exception as e:
                            print(f"Failed to delete status message: {e}")
                        return
                except Exception as e:
                    print(f"Primary API {primary_api['name']} exhausted all payloads and failed: {str(e)}")
                    last_error = e
        
                                                           
        for api_index, api_config in enumerate(self.cobalt_apis):
                                                                         
            if api_index == 0 and not should_skip_primary:
                continue
                
                                                             
            if api_config.get("instagram_only") and "instagram.com" not in url:
                continue
                                                                                          
            if "instagram.com" in url and not api_config.get("instagram_only"):
                                                                  
                has_instagram_api = any(api.get("instagram_only") for api in self.cobalt_apis)
                if has_instagram_api and api_index == 0:
                                                                                  
                    continue
            
            print(f"Trying API {api_index + 1}/{len(self.cobalt_apis)}: {api_config['name']}")
            await self._update_status(context, "Downloading file...", f"trying api {api_index + 1}/{len(self.cobalt_apis)}: {api_config['name']}")
            
                                                      
            if api_config.get("instagram_only") and "instagram.com" in url:
                try:
                    result = await self._try_instagram_embedez_api(context, url, api_config, forced_quality=quality, forced_format=file_format)
                    if result:
                        try:
                            if hasattr(context, 'message') and context.message:
                                await context.message.delete()
                        except Exception as e:
                            print(f"Failed to delete original message: {e}")
                        try:
                            if hasattr(context, 'status_message') and context.status_message:
                                await context.status_message.delete()
                        except Exception as e:
                            print(f"Failed to delete status message: {e}")
                        return
                except Exception as e:
                    print(f"Instagram embedez API {api_config['name']} failed: {str(e)}")
                    last_error = e
                    continue
            else:
                                                                         
                headers = {
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                }
                if api_config.get("key"):
                    headers["Authorization"] = f"Api-Key {api_config['key']}"
                try:
                    result = await self._try_download_with_quality(context, url, headers, api_config, send_status=False, forced_quality=quality, forced_format=file_format)
                    if result:
                        try:
                            if hasattr(context, 'message') and context.message:
                                await context.message.delete()
                        except Exception as e:
                            print(f"Failed to delete original message: {e}")
                        try:
                            if hasattr(context, 'status_message') and context.status_message:
                                await context.status_message.delete()
                        except Exception as e:
                            print(f"Failed to delete status message: {e}")
                        return
                except Exception as e:
                    print(f"API {api_config['name']} failed: {str(e)}")
                    last_error = e
                    continue
        
        user_friendly_error = "An unknown error occurred."
        if last_error:
            err_str = str(last_error)
            err_lower = err_str.lower()
            if any(x in err_lower for x in ["timeout", "timed out"]):
                user_friendly_error = "Connection to the API timed out. The service may be temporarily unavailable."
            elif "connection error" in err_lower:
                user_friendly_error = "Could not connect to the API server. Please check your internet connection or try again later."
            elif "tunnel expired" in err_lower:
                user_friendly_error = "The download tunnel expired before the file could be retrieved. Please try again."
            elif "unsupported mimetype" in err_lower or "unexpected mimetype" in err_lower:
                user_friendly_error = "The server returned an unexpected response. This may be a temporary issue or maintenance. Please try again later."
            elif "invalid_body" in err_lower:
                user_friendly_error = "The server could not process your request. This may be a bug or an unsupported link."
            elif "api error" in err_lower:
                m = re.search(r"api error from [^:]+: ([\w\.\-]+)", err_str)
                if m:
                    code = m.group(1)
                    user_friendly_error = f"API error: `{code}`. This may be a temporary issue or an unsupported link."
                else:
                    user_friendly_error = err_str
            else:
                user_friendly_error = err_str
        embed = self._styled_embed(
            title="Sorry, we couldn't process your link",
            description=(
                f"**We tried all available download servers, but none succeeded.**\n\n"
                f"**Original link:** [Click here]({url})\n\n"
                f"**Reason:** {user_friendly_error}\n\n"
                "If this keeps happening, the link may be unsupported or temporarily unavailable. "
                "You can try again later, or report this issue to the bot owner."
            ),
            color=0xE02B2B
        )
        await self._send_response(context, embed=embed)
    
    def _transform_instagram_url(self, url: str) -> str:
        """Transform Instagram URLs to embedez.com format to avoid signature mismatch issues."""
                                                                 
        instagram_pattern = r'https?://(?:www\.)?instagram\.com/(?:p/|reel/|reels/|stories/[^/]+/)([A-Za-z0-9_-]+)/?'
        
        match = re.match(instagram_pattern, url)
        if match:
            post_id = match.group(1)
                                                       
            transformed_url = f"https://instagram.embedez.com/{post_id}"
            print(f"Transformed Instagram URL: {url} -> {transformed_url}")
            return transformed_url
                                                              
        return url
    
    def _clean_instagram_title(self, title: str) -> str:
        """Clean up Instagram title by removing common artifacts and patterns."""
        if not title:
            return title
        
                                                                                
        title = re.sub(r'^[^:]+\s+on\s+Instagram:\s*', '', title, flags=re.IGNORECASE)
        
                                                                
        if title.startswith('"') and title.endswith('"'):
            title = title[1:-1]
        
                                                   
        if len(title) > 200:
            title = title[:197] + "..."
        
        return title.strip()
    
    async def _try_instagram_embedez_api(self, context: Context, url: str, api_config: dict, forced_quality: str = None, forced_format: str = None) -> bool:
        """Try Instagram embedez CDN API that mimics Cobalt API response format."""
        try:
                                                       
            transformed_url = self._transform_instagram_url(url)
            if transformed_url == url:
                print("URL transformation failed, not an Instagram URL")
                return False
            
            await self._update_status(context, "Downloading file...", f"Contacting {api_config['name']}...")
            
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
                'Accept-Encoding': 'gzip, deflate, br',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1'
            }
            
            session = await self.get_session()
            async with session.get(transformed_url, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as response:
                    if response.status == 200:
                        content_type = response.headers.get('Content-Type', '').lower()
                        
                                                             
                        if any(media_type in content_type for media_type in ['video/', 'image/', 'audio/']):
                            print(f"Direct media file detected from embedez: {content_type}")
                            
                                                                           
                            filename = "instagram_media"
                            if 'video' in content_type:
                                filename += '.mp4'
                            elif 'image' in content_type:
                                filename += '.jpg'
                            else:
                                filename += '.mp4'
                            
                                                                 
                            fake_cobalt_response = {
                                "status": "redirect",
                                "url": transformed_url,
                                "filename": filename
                            }
                            
                            print(f"Instagram embedez response: {fake_cobalt_response}")
                            await self._update_status(context, "Downloading file...", f"Received response from {api_config['name']}, processing...")
                            
                            return await self._handle_cobalt_response(context, fake_cobalt_response, url, requested_format=forced_format)
                        
                        else:
                                                                                       
                            html_content = await response.text()
                            media_urls = self._extract_instagram_media_urls(html_content)
                            
                            if media_urls:
                                print(f"Found {len(media_urls)} media URLs from embedez page")
                                
                                if len(media_urls) == 1:
                                                                             
                                    media_item = media_urls[0]
                                    media_url = media_item.get('url')
                                    media_type = media_item.get('type', 'video')
                                    
                                    if media_url:
                                        filename = f"instagram_{media_type}.{'mp4' if media_type == 'video' else 'jpg'}"
                                        
                                        fake_cobalt_response = {
                                            "status": "redirect",
                                            "url": media_url,
                                            "filename": filename
                                        }
                                        
                                        print(f"Instagram embedez single media response: {fake_cobalt_response}")
                                        return await self._handle_cobalt_response(context, fake_cobalt_response, url, requested_format=forced_format)
                                
                                else:
                                                                             
                                    picker_items = []
                                    for i, media_item in enumerate(media_urls[:10]):                     
                                        picker_items.append({
                                            "type": media_item.get('type', 'video'),
                                            "url": media_item['url'],
                                            "thumb": media_item.get('thumb')                      
                                        })
                                    
                                    fake_cobalt_response = {
                                        "status": "picker",
                                        "picker": picker_items
                                    }
                                    
                                    print(f"Instagram embedez picker response: {len(picker_items)} items")
                                    return await self._handle_cobalt_response(context, fake_cobalt_response, url, requested_format=forced_format)
                            
                            else:
                                print("No media URLs found in embedez page")
                                return False
                    
                    else:
                        print(f"Instagram embedez failed with status: {response.status}")
                        return False
                        
        except Exception as e:
            print(f"Instagram embedez API error: {e}")
            return False
    
    def _extract_instagram_media_urls(self, html_content: str) -> list[dict]:
        """Extract media URLs from Instagram embedez.com page."""
        media_urls = []
        try:
                                                       
            video_patterns = [
                r'<video[^>]*src="([^"]+)"',
                r'<source[^>]*src="([^"]+)"[^>]*type="video',
                r'videoUrl["\s]*:["\s]*"([^"]+)"',
                r'"video_url"["\s]*:["\s]*"([^"]+)"',
                r'"videoUrl"["\s]*:["\s]*"([^"]+)"',
                r'data-video["\s]*=["\s]*"([^"]+)"',
                r'video-src["\s]*=["\s]*"([^"]+)"'
            ]
            
            for pattern in video_patterns:
                matches = re.findall(pattern, html_content, re.IGNORECASE)
                for match in matches:
                    if match and 'http' in match and match not in [item['url'] for item in media_urls]:
                        media_urls.append({'url': match, 'type': 'video'})
            
                                                                            
            image_patterns = [
                r'<img[^>]*src="([^"]+)"',
                r'imageUrl["\s]*:["\s]*"([^"]+)"',
                r'"image_url"["\s]*:["\s]*"([^"]+)"',
                r'"imageUrl"["\s]*:["\s]*"([^"]+)"',
                r'data-image["\s]*=["\s]*"([^"]+)"',
                r'img-src["\s]*=["\s]*"([^"]+)"'
            ]
            
            for pattern in image_patterns:
                matches = re.findall(pattern, html_content, re.IGNORECASE)
                for match in matches:
                    if (match and 'http' in match and 
                        not any(skip in match.lower() for skip in ['logo', 'icon', 'avatar', 'placeholder']) and
                        match not in [item['url'] for item in media_urls]):
                        media_urls.append({'url': match, 'type': 'image'})
            
                                                              
            json_patterns = [
                r'"url"["\s]*:["\s]*"([^"]+\.(?:mp4|jpg|jpeg|png|webp|gif))"',
                r'"src"["\s]*:["\s]*"([^"]+\.(?:mp4|jpg|jpeg|png|webp|gif))"'
            ]
            
            for pattern in json_patterns:
                matches = re.findall(pattern, html_content, re.IGNORECASE)
                for match in matches:
                    if match and 'http' in match and match not in [item['url'] for item in media_urls]:
                        media_type = 'video' if match.lower().endswith('.mp4') else 'image'
                        media_urls.append({'url': match, 'type': media_type})
            
            print(f"Extracted {len(media_urls)} media URLs from embedez page")
            return media_urls[:10]                          
            
        except Exception as e:
            print(f"Error extracting media URLs: {e}")
            return []
    
    async def _handle_instagram_reel(self, context: Context, ddinstagram_url: str, original_url: str) -> None:
        """Handle Instagram reel by providing direct download link and scraping the title."""
                                                                                            
        username = None
        bio = None
        caption = None
        
        try:
                                                                       
            mobile_headers = {
                'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 15_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.0 Mobile/15E148 Safari/604.1',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
                'Accept-Encoding': 'gzip, deflate, br',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1',
            }
            
            session = await self.get_session()
            async with session.get(original_url, headers=mobile_headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        html = await resp.text()
                    

        except Exception as e:
            print(f"Failed to scrape Instagram data: {e}")

                                                                   
        await self._send_response(context, content=ddinstagram_url)

        embed = self._styled_embed(
            title="Instagram Reel",
            description="Direct download link (bypasses signature expiration issues)"
        )
        await self._send_response(context, embed=embed)
        
                                                                      
        try:
            if hasattr(context, 'message') and context.message:
                await context.message.delete()
        except Exception as e:
            print(f"Failed to delete original message: {e}")
       
    
    async def _try_download_with_exhaustive_payloads(self, context: Context, url: str, headers: dict, api_config: dict, send_status: bool = True, forced_quality: str = None, forced_format: str = None) -> bool:
        """Try ALL possible payload combinations on a single API before giving up."""
        if send_status:
            status_embed = self._styled_embed(
                title="Downloading file...",
                description="Please wait while the file is being downloaded."
            )
            await self._send_response(context, embed=status_embed)

        async def _call_api(payload: dict) -> dict:
            await self._update_status(context, "Downloading file...", f"Contacting {api_config['name']}...")
            session = await self.get_session()
            async with session.post(
                f"{api_config['url']}/",
                headers=headers,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=30)
            ) as response:
                    if response.status == 200:
                        data = await response.json()
                        print(f"Cobalt response from {api_config['name']}: {json.dumps(data, indent=2)}")
                        await self._update_status(context, "Downloading file...", f"Received response from {api_config['name']}, processing...")
                        return data
                    elif response.status == 401:
                        raise Exception(f"Authentication failed for {api_config['name']}")
                    elif response.status == 429:
                        raise Exception(f"Rate limited on {api_config['name']}")
                    else:
                        try:
                            error_data = await response.json()
                            error_msg = error_data.get('error', {}).get('code', 'Unknown error')
                        except Exception:
                            error_msg = f"HTTP {response.status}"
                        raise Exception(f"API error from {api_config['name']}: {error_msg}")

        def _build_payload_variants(base: dict, q_norm: str | None, fmt: str | None, is_audio: bool) -> list[dict]:
            """Generate payload variants to satisfy different Cobalt-compatible schemas."""
            variants = []
            if is_audio:
                                                       
                afmt = fmt or "mp3"
                                                         
                variants.append({**base, "downloadMode": "audio", "audioFormat": afmt, "audioBitrate": 320})
                                                            
                variants.append({**base, "downloadMode": "audio", "audioFormat": afmt, "audioBitrate": "320"})
                                                              
                variants.append({**base, "downloadMode": "audio", "audioFormat": afmt})
                                                                                               
                variants.append({**base, "downloadMode": "auto", "audioFormat": afmt})
                                                                
                variants.append({**base, "downloadMode": "audio", "fileType": afmt})
                                        
                variants.append({**base, "downloadMode": "auto", "fileType": afmt})
                return variants

                        
                                
            q_num = None
            if q_norm:
                                                                  
                try:
                    q_num = int(re.match(r"(\d{3,4})", q_norm).group(1)) if re.match(r"(\d{3,4})", q_norm) else None
                except Exception:
                    q_num = None

                                     
            def with_format_fields(payload: dict) -> list[dict]:
                out = [payload.copy()]
                if fmt in ("mp4", "webm"):
                    p2 = payload.copy()
                                                                      
                    p2.pop("videoFormat", None)
                    p2["fileType"] = fmt
                    out.append(p2)
                return out

                                            
            base_video = {**base, "downloadMode": "video"}
            if fmt in ("mp4", "webm"):
                base_video["videoFormat"] = fmt
                                                       
            base_video.setdefault("audioFormat", "mp3")

                                             
            quality_kv_options = []
            if q_norm:
                quality_kv_options.extend([
                    ("videoQuality", q_norm),                         
                ])
                if q_num:
                    quality_kv_options.extend([
                        ("videoQuality", str(q_num)),            
                        ("videoQuality", q_num),                     
                        ("vQuality", str(q_num)),                     
                        ("quality", str(q_num)),                   
                    ])

                                                                
            if not quality_kv_options:
                for p in with_format_fields(base_video):
                    variants.append(p)
            else:
                for key, val in quality_kv_options:
                    p = base_video.copy()
                    p[key] = val
                    for pf in with_format_fields(p):
                        variants.append(pf)

                                                                         
            base_auto = {**base, "downloadMode": "auto"}
            base_auto.setdefault("audioFormat", "mp3")
            if fmt in ("mp4", "webm"):
                base_auto["videoFormat"] = fmt
            if not quality_kv_options:
                for p in with_format_fields(base_auto):
                    variants.append(p)
            else:
                for key, val in quality_kv_options:
                    p = base_auto.copy()
                    p[key] = val
                    for pf in with_format_fields(p):
                        variants.append(pf)

            return variants

        def _normalize_quality(q: str | None) -> str | None:
            if not q:
                return None
            q = str(q).lower()
            if q.isdigit():
                return f"{q}p"
            m = re.match(r"^(\d{3,4})p$", q)
            if m:
                return m.group(0)
            m2 = re.match(r"^(\d{3,4})", q)
            if m2:
                return f"{m2.group(1)}p"
            return None

        try:
            print(f"[EXHAUSTIVE] Starting comprehensive payload testing on {api_config['name']}")
            
                                                     
            all_payloads = []
            
                          
            base_payload = {
                "url": url,
                "filenameStyle": "basic",
                "disableMetadata": False
            }
            
                                                                           
            if forced_quality or forced_format:
                q = _normalize_quality(forced_quality)
                audio_formats = ["mp3", "m4a", "ogg", "opus", "wav", "flac"]
                video_formats = ["mp4", "webm"]
                is_audio = forced_format in audio_formats
                
                print(f"[EXHAUSTIVE] User specified quality={forced_quality}, format={forced_format}")
                forced_variants = _build_payload_variants(base_payload, q, forced_format if forced_format in (video_formats + audio_formats) else None, is_audio)
                all_payloads.extend(forced_variants)
                
                                                         
                if is_audio and forced_format != "mp3":
                    mp3_variants = _build_payload_variants(base_payload, q, "mp3", True)
                    all_payloads.extend(mp3_variants)
            
                                              
            if "instagram.com" in url:
                standard_payloads = [
                                                                    
                    {"url": url},
                                                          
                    {"url": url, "downloadMode": "auto", "videoQuality": "max", "audioFormat": "best"},
                                                                         
                    {"url": url, "downloadMode": "auto", "alwaysProxy": False},
                                                            
                    {"url": url, "downloadMode": "video", "videoQuality": "1080p", "audioFormat": "mp3"},
                    {"url": url, "downloadMode": "video", "videoQuality": "720p", "audioFormat": "mp3"},
                    {"url": url, "downloadMode": "auto", "videoFormat": "mp4", "audioFormat": "mp3"},
                ]
            else:
                standard_payloads = [
                                                        
                    {"url": url},
                                                   
                    {"url": url, "downloadMode": "auto"},
                                                                 
                    {"url": url, "downloadMode": "auto", "audioFormat": "mp3"},
                                                      
                    {"url": url, "downloadMode": "video", "videoQuality": "1080p", "audioFormat": "mp3"},
                    {"url": url, "downloadMode": "video", "videoQuality": "720p", "audioFormat": "mp3"},
                    {"url": url, "downloadMode": "video", "videoQuality": "480p", "audioFormat": "mp3"},
                                                  
                    {"url": url, "downloadMode": "auto", "videoFormat": "mp4", "audioFormat": "mp3"},
                    {"url": url, "downloadMode": "auto", "videoFormat": "webm", "audioFormat": "mp3"},
                                               
                    {"url": url, "downloadMode": "audio", "audioFormat": "mp3"},
                    {"url": url, "downloadMode": "audio", "audioFormat": "mp3", "audioBitrate": 320},
                ]
            
            all_payloads.extend(standard_payloads)
            
                                                                                       
            if not forced_quality and not forced_format:
                comprehensive_payloads = []
                qualities = ["2160p", "1440p", "1080p", "720p", "480p", "360p"]
                video_formats = ["mp4", "webm"]
                audio_formats = ["mp3", "m4a", "opus"]
                
                                            
                for q in qualities:
                    for fmt in video_formats:
                        comprehensive_payloads.extend([
                            {"url": url, "downloadMode": "video", "videoQuality": q, "videoFormat": fmt, "audioFormat": "mp3"},
                            {"url": url, "downloadMode": "auto", "videoQuality": q, "videoFormat": fmt, "audioFormat": "mp3"},
                        ])
                
                                           
                for afmt in audio_formats:
                    comprehensive_payloads.extend([
                        {"url": url, "downloadMode": "audio", "audioFormat": afmt},
                        {"url": url, "downloadMode": "audio", "audioFormat": afmt, "audioBitrate": 320},
                        {"url": url, "downloadMode": "auto", "audioFormat": afmt},
                    ])
                
                all_payloads.extend(comprehensive_payloads)
            
                                                      
            seen = set()
            unique_payloads = []
            for payload in all_payloads:
                payload_key = json.dumps(payload, sort_keys=True)
                if payload_key not in seen:
                    seen.add(payload_key)
                    unique_payloads.append(payload)
            
            print(f"[EXHAUSTIVE] Generated {len(unique_payloads)} unique payload combinations")
            
                                                 
            last_error = None
            for idx, payload in enumerate(unique_payloads, 1):
                try:
                    print(f"[EXHAUSTIVE] Trying payload {idx}/{len(unique_payloads)}: {json.dumps({k:v for k,v in payload.items() if k != 'url'}, default=str)}")
                    await self._update_status(context, "Downloading file...", f"trying payload {idx}/{len(unique_payloads)} on {api_config['name']}")
                    
                    data = await _call_api(payload)
                    
                    if data and data.get("status") in ["tunnel", "redirect", "picker"]:
                        print(f"[EXHAUSTIVE] SUCCESS with payload {idx}: {data.get('status')}")
                        return await self._handle_cobalt_response(context, data, url, requested_format=forced_format)
                    elif data and data.get("status") == "error":
                        error_code = data.get("error", {}).get("code", "unknown")
                        print(f"[EXHAUSTIVE] Payload {idx} returned error: {error_code}")
                        last_error = Exception(f"API error: {error_code}")
                        continue
                    else:
                        print(f"[EXHAUSTIVE] Payload {idx} returned unexpected status: {data.get('status') if data else 'None'}")
                        continue
                        
                except Exception as e:
                    print(f"[EXHAUSTIVE] Payload {idx} failed: {e}")
                    last_error = e
                                                                                                      
                    if "invalid_body" in str(e).lower():
                        continue
                    elif "timeout" in str(e).lower() or "connection" in str(e).lower():
                                                                         
                        continue
                    else:
                                                                               
                        continue
            
            print(f"[EXHAUSTIVE] All {len(unique_payloads)} payloads failed on {api_config['name']}")
            if last_error:
                raise last_error
            else:
                raise Exception(f"All payloads exhausted on {api_config['name']}")
                
        except Exception as e:
            print(f"[EXHAUSTIVE] Final error from {api_config['name']}: {e}")
            raise e
        
        return False

    async def _try_download_with_quality(self, context: Context, url: str, headers: dict, api_config: dict, send_status: bool = True, forced_quality: str = None, forced_format: str = None) -> bool:
        """Try downloading with specific quality/format when provided; otherwise use default/best quality."""
        if send_status:
            status_embed = self._styled_embed(
                title="Downloading file...",
                description="Please wait while the file is being downloaded."
            )
            await self._send_response(context, embed=status_embed)

        async def _call_api(payload: dict) -> dict:
            await self._update_status(context, "Downloading file...", f"Contacting {api_config['name']}...")
            session = await self.get_session()
            async with session.post(
                f"{api_config['url']}/",
                headers=headers,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=30)
            ) as response:
                    if response.status == 200:
                        data = await response.json()
                        print(f"Cobalt response from {api_config['name']}: {json.dumps(data, indent=2)}")
                        await self._update_status(context, "Downloading file...", f"Received response from {api_config['name']}, processing...")
                        return data
                    elif response.status == 401:
                        raise Exception(f"Authentication failed for {api_config['name']}")
                    elif response.status == 429:
                        raise Exception(f"Rate limited on {api_config['name']}")
                    else:
                        try:
                            error_data = await response.json()
                            error_msg = error_data.get('error', {}).get('code', 'Unknown error')
                        except Exception:
                            error_msg = f"HTTP {response.status}"
                        raise Exception(f"API error from {api_config['name']}: {error_msg}")

        def _build_payload_variants(base: dict, q_norm: str | None, fmt: str | None, is_audio: bool) -> list[dict]:
            """Generate payload variants to satisfy different Cobalt-compatible schemas."""
            variants = []
            if is_audio:
                                                       
                afmt = fmt or "mp3"
                                                         
                variants.append({**base, "downloadMode": "audio", "audioFormat": afmt, "audioBitrate": 320})
                                                            
                variants.append({**base, "downloadMode": "audio", "audioFormat": afmt, "audioBitrate": "320"})
                                                              
                variants.append({**base, "downloadMode": "audio", "audioFormat": afmt})
                                                                                               
                variants.append({**base, "downloadMode": "auto", "audioFormat": afmt})
                                                                
                variants.append({**base, "downloadMode": "audio", "fileType": afmt})
                                        
                variants.append({**base, "downloadMode": "auto", "fileType": afmt})
                return variants

                        
                                
            q_num = None
            if q_norm:
                                                                  
                try:
                    q_num = int(re.match(r"(\d{3,4})", q_norm).group(1)) if re.match(r"(\d{3,4})", q_norm) else None
                except Exception:
                    q_num = None

                                     
            def with_format_fields(payload: dict) -> list[dict]:
                out = [payload.copy()]
                if fmt in ("mp4", "webm"):
                    p2 = payload.copy()
                                                                      
                    p2.pop("videoFormat", None)
                    p2["fileType"] = fmt
                    out.append(p2)
                return out

                                            
            base_video = {**base, "downloadMode": "video"}
            if fmt in ("mp4", "webm"):
                base_video["videoFormat"] = fmt
                                                       
            base_video.setdefault("audioFormat", "mp3")

                                             
            quality_kv_options = []
            if q_norm:
                quality_kv_options.extend([
                    ("videoQuality", q_norm),                         
                ])
                if q_num:
                    quality_kv_options.extend([
                        ("videoQuality", str(q_num)),            
                        ("videoQuality", q_num),                     
                        ("vQuality", str(q_num)),                     
                        ("quality", str(q_num)),                   
                    ])

                                                                
            if not quality_kv_options:
                for p in with_format_fields(base_video):
                    variants.append(p)
            else:
                for key, val in quality_kv_options:
                    p = base_video.copy()
                    p[key] = val
                    for pf in with_format_fields(p):
                        variants.append(pf)

                                                                         
            base_auto = {**base, "downloadMode": "auto"}
            base_auto.setdefault("audioFormat", "mp3")
            if fmt in ("mp4", "webm"):
                base_auto["videoFormat"] = fmt
            if not quality_kv_options:
                for p in with_format_fields(base_auto):
                    variants.append(p)
            else:
                for key, val in quality_kv_options:
                    p = base_auto.copy()
                    p[key] = val
                    for pf in with_format_fields(p):
                        variants.append(pf)

            return variants

        def _normalize_quality(q: str | None) -> str | None:
            if not q:
                return None
            q = str(q).lower()
            if q.isdigit():
                return f"{q}p"
            m = re.match(r"^(\d{3,4})p$", q)
            if m:
                return m.group(0)
            m2 = re.match(r"^(\d{3,4})", q)
            if m2:
                return f"{m2.group(1)}p"
            return None

        try:
                         
            if forced_quality or forced_format:
                q = _normalize_quality(forced_quality)
                audio_formats = ["mp3", "m4a", "ogg", "opus", "wav", "flac"]
                video_formats = ["mp4", "webm"]
                payload = {
                    "url": url,
                    "filenameStyle": "basic",
                    "disableMetadata": False
                }
                                                                                          
                is_audio = forced_format in audio_formats
                variants = _build_payload_variants(payload, q, forced_format if forced_format in (video_formats + audio_formats) else None, is_audio)

                last_err = None
                data = None
                                      
                                                                                                               
                                                                                                             
                preferred_tried = False
                if is_audio and q:
                    preferred = {**payload, "downloadMode": "video", "audioFormat": forced_format, "videoQuality": q}
                    try:
                        preferred_tried = True
                        print(f"Trying preferred audio+quality payload: {json.dumps({k:v for k,v in preferred.items() if k != 'url'}, default=str)}")
                        data = await _call_api(preferred)
                    except Exception as e:
                                                                               
                        if "invalid_body" in str(e).lower():
                            data = None
                        else:
                            raise

                for idx, pl in enumerate(variants, start=1):
                    try:
                        print(f"Trying payload variant {idx}/{len(variants)}: {json.dumps({k:v for k,v in pl.items() if k != 'url'}, default=str)}")
                        data = await _call_api(pl)
                        break
                    except Exception as e:
                        last_err = e
                                                                                 
                        if "invalid_body" in str(e).lower():
                            continue
                        else:
                            raise
                                                                                                               
                if data is None and last_err is not None and is_audio and (forced_format or "mp3") != "mp3":
                    try:
                        print("Audio format not accepted by API, falling back to mp3...")
                        mp3_variants = _build_payload_variants(payload, q, "mp3", True)
                        for idx, pl in enumerate(mp3_variants, start=1):
                            try:
                                print(f"Trying mp3 fallback variant {idx}/{len(mp3_variants)}: {json.dumps({k:v for k,v in pl.items() if k != 'url'}, default=str)}")
                                data = await _call_api(pl)
                                break
                            except Exception as e2:
                                last_err = e2
                                if "invalid_body" in str(e2).lower():
                                    continue
                                else:
                                    raise
                    except Exception:
                        pass
                if data is None and last_err is not None:
                                                               
                    raise last_err
                if data.get("status") in ["tunnel", "redirect"]:
                                                                                  
                    return await self._handle_cobalt_response(context, data, url, requested_format=forced_format)
                else:
                    return await self._handle_cobalt_response(context, data, url, requested_format=forced_format)

                                                                                         
            await self._update_status(context, "Downloading file...", f"Contacting {api_config['name']}...")
            
                                                            
                                                                          
            if "tiktok.com" in url:
                working_payloads = [
                                                             
                    {
                        "url": url
                    },
                                        
                    {
                        "url": url,
                        "downloadMode": "auto"
                    }
                ]
            elif "instagram.com" in url:
                working_payloads = [
                                                                    
                    {
                        "url": url
                    },
                                                          
                    {
                        "url": url,
                        "downloadMode": "auto",
                        "videoQuality": "max",
                        "audioFormat": "best"
                    },
                                                                   
                    {
                        "url": url,
                        "downloadMode": "auto",
                        "alwaysProxy": False
                    }
                ]
            else:
                working_payloads = [
                                                           
                    {
                        "url": url
                    },
                                                      
                    {
                        "url": url,
                        "downloadMode": "auto"
                    },
                                                                    
                    {
                        "url": url,
                        "downloadMode": "auto",
                        "audioFormat": "mp3"
                    }
                ]
            
            session = await self.get_session()
            best_data = None
            best_download_url = None
            
                                                     
            for idx, payload in enumerate(working_payloads, 1):
                    try:
                        print(f"[{api_config['name']}] Trying payload {idx}/{len(working_payloads)}: {json.dumps(payload, default=str)}")
                        async with session.post(
                            f"{api_config['url']}/",
                            headers=headers,
                            json=payload,
                            timeout=aiohttp.ClientTimeout(total=30)
                        ) as response:
                            if response.status == 200:
                                data = await response.json()
                                print(f"[{api_config['name']}] Payload {idx} SUCCESS: {data.get('status', 'unknown')}")
                                
                                if data.get("status") in ["tunnel", "redirect"]:
                                    download_url = data.get('url')
                                    
                                                                                                 
                                    if "instagram.com" in url:
                                        print(f"[{api_config['name']}] Instagram URL detected - processing immediately")
                                        return await self._handle_cobalt_response(context, data, url)
                                    
                                                                                                               
                                    if "tunnel" not in (download_url or ""):
                                        print(f"[{api_config['name']}] Got direct URL - using immediately")
                                        best_data = data
                                        best_download_url = download_url
                                        break
                                    elif not best_data:
                                        print(f"[{api_config['name']}] Got tunnel URL - storing as fallback")
                                        best_data = data
                                        best_download_url = download_url
                                elif data.get("status") == "picker":
                                    print(f"[{api_config['name']}] Got picker response - using immediately")
                                    best_data = data
                                    break
                                else:
                                    print(f"[{api_config['name']}] Unexpected status: {data.get('status')}")
                            else:
                                print(f"[{api_config['name']}] Payload {idx} failed with status: {response.status}")
                                                                         
                                continue
                    except Exception as e:
                        print(f"[{api_config['name']}] Payload {idx} error: {e}")
                        continue
            
                                          
            if best_data:
                print(f"[{api_config['name']}] Using best result. Download URL: {best_download_url}")
                await self._update_status(context, "Downloading file...", f"Processing response from {api_config['name']}...")
                
                                                                                   
                is_tiktok = "tiktok.com" in url.lower()
                
                if best_data.get("status") in ["tunnel", "redirect"]:
                    if not is_tiktok:
                                                         
                        print(f"[{api_config['name']}] Checking file size for: {best_download_url}")
                    else:
                        print(f"[{api_config['name']}] Skipping size check for TikTok (optimization)")
                                                                                  
                    print(f"[{api_config['name']}] Proceeding with download (will handle size during upload)...")
                    return await self._handle_cobalt_response(context, best_data, url)
                else:
                    print(f"[{api_config['name']}] Handling non-redirect response...")
                    return await self._handle_cobalt_response(context, best_data, url)
            else:
                                                        
                print(f"[{api_config['name']}] All payloads failed - moving to next API")
                raise Exception(f"No working payloads for {api_config['name']}")
        except asyncio.TimeoutError:
            raise Exception(f"Timeout for {api_config['name']}")
        except aiohttp.ClientError as e:
            raise Exception(f"Connection error for {api_config['name']}: {str(e)}")
        except Exception as e:
            raise e
        return False

    def _quality_fallbacks(self, requested: str | None) -> list[str]:
        """Return a stepped list of lower qualities to try, based on the requested one."""
        if not requested:
            return []
        try:
            rq = requested.lower()
            if rq.isdigit():
                rq += 'p'
            order = [
                '2160p','1440p','1080p','720p','480p','360p','240p'
            ]
            if rq not in order:
                                              
                m = re.match(r"(\d{3,4})", rq)
                if m:
                    rq = f"{m.group(1)}p"
            if rq in order:
                idx = order.index(rq)
                return order[idx+1:]
        except Exception:
            pass
        return ['720p','480p','360p']

    async def _fallback_download_lower_quality(self, context: Context, original_post_url: str, preferred_format: str | None) -> bool:
        """Attempt to fetch and download a lower quality when the tunnel produced 0 bytes."""
        fallbacks = self._quality_fallbacks(getattr(context, 'remorse_requested_quality', None))
        if not fallbacks:
            return False
                                  
        setattr(context, '_remorse_disable_quality_fallback', True)
        try:
            for q in fallbacks:
                await self._update_status(context, "Downloading file...", f"Requested quality unavailable. Trying {q}...")
                for api_index, api_config in enumerate(self.cobalt_apis):
                    headers = {
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                    }
                    if api_config.get("key"):
                        headers["Authorization"] = f"Api-Key {api_config['key']}"
                    payloads = [
                        {"url": original_post_url, "downloadMode": "video", "audioFormat": "mp3", "videoQuality": q, "filenameStyle": "basic", "disableMetadata": False},
                        {"url": original_post_url, "downloadMode": "auto", "audioFormat": "mp3", "videoQuality": q, "filenameStyle": "basic", "disableMetadata": False},
                    ]
                                                                               
                    if preferred_format in ("mp4", "webm"):
                        for p in payloads:
                            p["videoFormat"] = preferred_format
                    try:
                        session = await self.get_session()
                        for p in payloads:
                            await self._update_status(context, "Downloading file...", f"Contacting {api_config['name']} for {q}...")
                            async with session.post(f"{api_config['url']}/", headers=headers, json=p, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                                if resp.status == 200:
                                    data = await resp.json()
                                                                                                            
                                    context.remorse_requested_quality = q
                                    ok = await self._handle_cobalt_response(context, data, original_post_url, requested_format=preferred_format)
                                    if ok:
                                        return True
                                elif resp.status in (401, 429):
                                    break
                    except Exception as e:
                        print(f"fallback quality {q} on {api_config['name']} failed: {e}")
                        continue
        finally:
            setattr(context, '_remorse_disable_quality_fallback', False)
        return False

    async def _check_file_size_acceptable(self, download_url: str, context: Context = None) -> bool:
        """Check if a file size is acceptable for Discord upload without downloading the entire file.
        Uses the guild's actual filesize limit when available.
        """
        try:
                                                                                                         
            if "tunnel" in download_url or "api-cobalt" in download_url:
                print("Skipping size check for Cobalt tunnel URL - will check during download")
                return True
            
                                                         
            print(f"Checking file size for direct URL: {download_url}")
            session = await self.get_session()
            async with session.head(download_url, timeout=aiohttp.ClientTimeout(total=10)) as response:
                    print(f"HEAD request status: {response.status}")
                    if response.status == 200:
                        content_length = response.headers.get('Content-Length')
                        print(f"Content-Length header: {content_length}")
                        if content_length and int(content_length) > 0:
                            file_size = int(content_length)
                                                                                                     
                            max_size = 10 * 1024 * 1024
                            try:
                                if context and getattr(context, 'guild', None):
                                                                                         
                                    max_size = int(getattr(context.guild, 'filesize_limit', max_size) or max_size)
                            except Exception:
                                pass
                            print(f"File size: {file_size / (1024*1024):.2f}MB, Max: {max_size / (1024*1024):.2f}MB")
                            is_acceptable = file_size <= max_size
                            print(f"Size check result: {'ACCEPTABLE' if is_acceptable else 'TOO LARGE'}")
                            return is_acceptable
                        else:
                            print("No valid Content-Length, assuming acceptable")
                    else:
                        print(f"HEAD request failed with status {response.status}, assuming acceptable")
                                                                                                              
                    return True
        except Exception as e:
            print(f"Error checking file size: {e}")
                                                       
            return True

    async def _send_response(self, context: Context, *, embed: discord.Embed = None, content: str = None, file: discord.File = None, view: View = None, return_message: bool = False):
        """Send a response that works for both slash commands and prefix commands."""
        try:
                                                                   
            if hasattr(context, 'followup') and context.interaction:
                if file:
                    message = await context.followup.send(embed=embed, content=content, file=file, view=view)
                elif embed:
                    message = await context.followup.send(embed=embed, view=view)
                else:
                    message = await context.followup.send(content=content, view=view)
            else:
                                                      
                if file:
                    message = await context.send(embed=embed, content=content, file=file, view=view)
                elif embed:
                    message = await context.send(embed=embed, view=view)
                else:
                    message = await context.send(content=content, view=view)
            
            if return_message:
                return message
                
        except Exception:
                                                        
            if file:
                await context.send(embed=embed, content=content, file=file, view=view)
            elif embed:
                await context.send(embed=embed, view=view)
            else:
                await context.send(content=content, view=view)

    async def _handle_cobalt_response(self, context: Context, data: dict, original_url: str, requested_format: str = None) -> bool:
        """Handle the response from Cobalt API and send appropriate Discord message. Returns True if successful, False if should try next API.
        Adds a warning if the returned file format does not match the requested format."""
        status = data.get("status")
        if status == "tunnel" or status == "redirect":
            download_url = data.get('url')
            filename = data.get('filename', 'download')
                                                                     
            actual_ext = filename.split('.')[-1].lower() if '.' in filename else None
            warn_format = False
                                                                                
            req_format = requested_format if 'requested_format' in locals() else None
            if req_format and actual_ext and req_format != actual_ext:
                warn_format = True
            
                                                      
            result = await self._download_and_send_file(context, download_url, filename, original_url, 
                                                      cobalt_data=data)
                                                                         
            if warn_format:
                embed = self._styled_embed(
                    title="Format warning",
                    description=f"You requested `{req_format}` but the file is `{actual_ext}`. The site may not support the requested format for this link.",
                    color=0xF1C40F
                )
                await self._send_response(context, embed=embed)
            return result                                             
            
        elif status == "picker":
                                                                           
            picker_items = data.get("picker", [])
            if picker_items:
                if len(picker_items) == 1:
                                                                       
                    first_item = picker_items[0]
                    first_url = first_item.get("url")
                    first_type = first_item.get("type", "media")
                    
                    if first_url:
                                                                                 
                        embed = self._styled_embed(
                            title="Instagram Post"
                        )
                        embed.set_image(url=first_url)
                        embed.add_field(
                            name="Post",
                            value=f"[View original]({original_url})",
                            inline=False
                        )
                        
                                                            
                        await self._send_response(context, embed=embed)
                        
                                                         
                        try:
                            if hasattr(context, 'status_message') and context.status_message:
                                await context.status_message.delete()
                        except Exception as e:
                            print(f"Failed to delete status message: {e}")
                        
                        return True
                else:
                                                         
                    try:
                                                           
                        first_item = picker_items[0]
                        first_url = first_item.get("url")
                        first_type = first_item.get("type", "media")
                        
                        if first_url:
                                                                                                     
                            embed = self._styled_embed(
                                title=f"Instagram Post • 1/{len(picker_items)}"
                            )
                            embed.set_image(url=first_url)
                            embed.add_field(
                                name="Post",
                                value=f"[View original]({original_url})",
                                inline=False
                            )
                            
                                                    
                            view = MediaPaginationView(context, picker_items, original_url, self)
                            
                                                                                                
                            message = await self._send_response(context, embed=embed, view=view, return_message=True)
                            view.message = message
                            
                                                             
                            try:
                                if hasattr(context, 'status_message') and context.status_message:
                                    await context.status_message.delete()
                            except Exception as e:
                                print(f"Failed to delete status message: {e}")
                            
                            return True
                        
                    except Exception as e:
                        print(f"Error creating pagination view: {e}")
                                                                                 
                        first_item = picker_items[0]
                        first_url = first_item.get("url")
                        first_type = first_item.get("type", "media")
                        
                        if first_url:
                                                                
                            embed = self._styled_embed(
                                title="Instagram Post",
                                description="Unable to create pagination buttons, showing first image only.",
                                color=0xF1C40F
                            )
                            embed.set_image(url=first_url)
                            embed.add_field(
                                name="Post",
                                value=f"[View original]({original_url})",
                                inline=False
                            )
                            await self._send_response(context, embed=embed)
                            
                                                                 
                            if len(picker_items) > 1:
                                embed = self._styled_embed(
                                    title="Additional items found",
                                    description="Here are the remaining downloads:"
                                )
                                
                                remaining_items = picker_items[1:11]
                                for i, item in enumerate(remaining_items, 2):
                                    item_type = item.get("type", "unknown").title()
                                    item_url = item.get("url")
                                    if item_url:
                                        embed.add_field(
                                            name=f"{i}. {item_type}",
                                            value=f"[download link]({item_url})",
                                            inline=True
                                        )
                                
                                if len(picker_items) > 11:
                                    embed.add_field(
                                        name="note",
                                        value=f"showing 10 of {len(picker_items)} total items.",
                                        inline=False
                                    )
                                await self._send_response(context, embed=embed)
                            
                            return True
                
                                        
                if data.get("audio"):
                    audio_result = await self._download_and_send_file(context, data.get("audio"), data.get("audioFilename", "audio.mp3"), original_url, is_audio=True, cobalt_data=data)
                    if not audio_result:
                        return False                        
                return True           
            else:
                embed = self._styled_embed(
                    title="No items found",
                    description="No downloadable items were found.",
                    color=0xE02B2B
                )
                await self._send_response(context, embed=embed)
                return False                                
            
        elif status == "error":
            error_info = data.get("error", {})
            error_code = error_info.get("code", "unknown")
            error_context = error_info.get("context", {})
            
            embed = self._styled_embed(
                title="Processing error",
                description="Cobalt encountered an error while processing your request.",
                color=0xE02B2B
            )
            embed.add_field(
                name="Error code",
                value=f"`{error_code}`",
                inline=False
            )
            
                                                  
            if error_context.get("service"):
                embed.add_field(
                    name="Service",
                    value=error_context.get("service"),
                    inline=True
                )
            if error_context.get("limit"):
                embed.add_field(
                    name="Limit",
                    value=str(error_context.get("limit")),
                    inline=True
                )
            await self._send_response(context, embed=embed)
            return False                       
            
        else:
            embed = self._styled_embed(
                title="Unknown response",
                description=f"Received an unexpected response status: `{status}`",
                color=0xF1C40F
            )
            await self._send_response(context, embed=embed)
            return False                                  

    async def _download_and_send_file(self, context: Context, url: str, filename: str, original_url: str, is_audio: bool = False, cobalt_data: dict = None, show_status: bool = False) -> bool:
        """Download a file from the given URL and send it to Discord. Returns True if successful, False if should try next API."""
        file_path = None
                                                       
        site_name = self._get_site_name(original_url)
        try:
                                                               
            await self._update_status(context, "Downloading file...", "Starting download...")
            
                                  
            safe_filename = "".join(c for c in filename if c.isalnum() or c in (' ', '.', '_', '-')).rstrip()
            if not safe_filename:
                safe_filename = f"download_{hash(url) % 10000}"
                                                                                           
            try:
                import os as _os_mod
                name_root, name_ext = _os_mod.path.splitext(safe_filename)
                if is_audio and not name_ext:
                    safe_filename = f"{name_root or 'audio'}.mp3"
            except Exception:
                                                                 
                pass
            
            file_path = os.path.join(self.media_folder, safe_filename)
            print(f"Downloading to: {file_path}")
            
                                                                           
            session = await self.get_download_session()
                                                   
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Accept': '*/*',
                'Connection': 'keep-alive',
            }
            
                                                                                 
                                                                           
            is_tiktok = "tiktok.com" in original_url.lower()
            
                                                                                
            if is_tiktok:
                timeout_seconds = 180 if ("tunnel" in url or "api-cobalt" in url) else 120                                             
            else:
                timeout_seconds = 600 if ("tunnel" in url or "api-cobalt" in url) else 300                                             
            
                                                                 
            if "tunnel" in url or "api-cobalt" in url:
                if is_tiktok:
                    print(f"TikTok tunnel URL detected - using {timeout_seconds}s timeout for optimized download...")
                    await self._update_status(context, "Downloading file...", "Connecting to TikTok (optimized timeout)...")
                else:
                    print(f"Tunnel URL detected - using {timeout_seconds}s timeout for slow download...")
                    await self._update_status(context, "Downloading file...", "Connecting to tunnel URL (this may take a while)...")
            else:
                await self._update_status(context, "Downloading file...", "Connecting to download server...")
            
            async with session.get(url, headers=headers, allow_redirects=True, timeout=aiohttp.ClientTimeout(total=timeout_seconds)) as response:
                    print(f"Download response status: {response.status}")
                    print(f"Download response headers: {dict(response.headers)}")
                    
                    if response.status == 200:
                                                                    
                        await self._update_status(context, "Downloading file...", "Connection established, downloading...")
                        
                                                           
                        content_length = response.headers.get('Content-Length')
                        
                        if content_length and int(content_length) > 0:
                            expected_size = int(content_length)
                            print(f"Expected file size from Content-Length: {expected_size} bytes")
                        else:
                            expected_size = None
                            print("No size information available - will stream until complete")
                        
                                                                                
                        if content_length == "0":
                            print("WARNING: Content-Length is 0 - tunnel URL might be expired or invalid")
                                                                                                     
                        
                                                                                     
                        estimated_length = response.headers.get('estimated-content-length')
                        if estimated_length:
                            print(f"Estimated content length: {estimated_length}")
                                                                       
                            if not expected_size and estimated_length != "-1":
                                try:
                                    expected_size = int(estimated_length)
                                    print(f"Using estimated size: {expected_size} bytes")
                                except ValueError:
                                    pass
                        
                                                                                 
                        async with aiofiles.open(file_path, 'wb') as f:
                            total_downloaded = 0
                            chunk_count = 0
                            last_progress_report = 0
                            last_status_update = 0
                            last_percentage = 0
                            start_time = time.time()
                            
                                                                                                 
                            is_tunnel = "tunnel" in url or "api-cobalt" in url
                            
                                                                     
                            if is_tiktok:
                                progress_update_interval = 10 * 1024 * 1024                   
                                console_report_interval = 5 * 1024 * 1024                               
                                chunk_size = 2 * 1024 * 1024                                   
                            elif is_tunnel:
                                progress_update_interval = 5 * 1024 * 1024                   
                                console_report_interval = 2 * 1024 * 1024                   
                                chunk_size = 1 * 1024 * 1024                                      
                            else:
                                progress_update_interval = 20 * 1024 * 1024                   
                                console_report_interval = 10 * 1024 * 1024                   
                                chunk_size = 2 * 1024 * 1024                                      
                            
                                                                     
                            if is_tunnel and expected_size and expected_size > 0:
                                print(f"Starting tunnel download of {expected_size / (1024*1024):.1f} MB...")
                                await self._update_status(
                                    context,
                                    "Downloading file...",
                                    f"**Starting download** • {expected_size / (1024*1024):.1f} MB"
                                )
                            
                                                                      
                                                                                            
                            print(f"Starting chunk iteration with {chunk_size / 1024}KB chunks...")
                            chunk_num = 0
                            last_chunk_time = time.time()
                            download_cancelled = False
                            
                                                                         
                            async def check_download_stall():
                                nonlocal download_cancelled
                                while not download_cancelled:
                                    await asyncio.sleep(2)                         
                                    if download_cancelled:
                                        break
                                    elapsed = time.time() - last_chunk_time
                                                                     
                                    timeout = 8 if chunk_num <= 3 else 15
                                    if elapsed > timeout:
                                        print(f"STALL DETECTED: No chunk for {elapsed:.1f}s (chunk #{chunk_num})")
                                        download_cancelled = True
                                        break
                            
                                                             
                            stall_task = asyncio.create_task(check_download_stall())
                            
                            try:
                                async for chunk in response.content.iter_chunked(chunk_size):
                                    if download_cancelled:
                                        print("Download cancelled due to stall detection")
                                        raise asyncio.TimeoutError("Download stalled - no data received")
                                    
                                    chunk_num += 1
                                    current_time = time.time()
                                    
                                    if chunk_num == 1:
                                        print(f"Received first chunk: {len(chunk)} bytes")
                                    elif chunk_num == 2:
                                        print(f"Received second chunk: {len(chunk)} bytes (download progressing)")
                                    
                                    if chunk:
                                        await f.write(chunk)
                                        total_downloaded += len(chunk)
                                        chunk_count += 1
                                        last_chunk_time = current_time                                     
                                        
                                                                                   
                                        if chunk_count == 1:
                                            elapsed = time.time() - start_time
                                            print(f"First chunk written, total: {total_downloaded} bytes in {elapsed:.1f}s")
                                            if expected_size and expected_size > 0:
                                                await self._update_status(
                                                    context,
                                                    "Downloading file...",
                                                    f"**0%** • Download started • {expected_size / (1024*1024):.1f} MB total"
                                                )
                                        
                                                                                                      
                                        if total_downloaded - last_progress_report >= 500 * 1024:
                                            elapsed = time.time() - start_time
                                            speed_mbps = (total_downloaded / (1024*1024)) / elapsed if elapsed > 0 else 0
                                            print(f"Downloaded {total_downloaded / (1024*1024):.1f}MB so far... ({speed_mbps:.2f} MB/s)")
                                            last_progress_report = total_downloaded
                                        
                                                                                                                 
                                        update_threshold = 1 * 1024 * 1024 if is_tunnel else 5 * 1024 * 1024
                                        if total_downloaded - last_status_update >= update_threshold:
                                            if expected_size and expected_size > 0:
                                                progress_pct = (total_downloaded / expected_size) * 100
                                                elapsed = time.time() - start_time
                                                speed_kbps = (total_downloaded / 1024) / elapsed if elapsed > 0 else 0
                                                eta_seconds = ((expected_size - total_downloaded) / 1024 / speed_kbps) if speed_kbps > 0 else 0
                                                eta_minutes = eta_seconds / 60
                                                
                                                if is_tunnel:
                                                                                                                
                                                    progress_text = (
                                                        f"**{progress_pct:.0f}%** • "
                                                        f"{total_downloaded / (1024*1024):.1f}/{expected_size / (1024*1024):.1f} MB"
                                                    )
                                                    await self._update_status(
                                                        context,
                                                        "Downloading file...",
                                                        progress_text
                                                    )
                                                else:
                                                    await self._update_status(context, "Downloading file...", f"**{progress_pct:.0f}%** complete")
                                                last_percentage = progress_pct
                                            else:
                                                                                                
                                                elapsed = time.time() - start_time
                                                speed_kbps = (total_downloaded / 1024) / elapsed if elapsed > 0 else 0
                                                await self._update_status(
                                                    context, 
                                                    "Downloading file...", 
                                                    f"**{total_downloaded / (1024*1024):.1f} MB** downloaded • {speed_kbps:.0f} KB/s"
                                                )
                                            last_status_update = total_downloaded
                                    else:
                                        print("Received empty chunk - download complete")
                                        break
                            finally:
                                                             
                                download_cancelled = True
                                stall_task.cancel()
                                try:
                                    await stall_task
                                except asyncio.CancelledError:
                                    pass
                        
                        print(f"Total downloaded: {total_downloaded} bytes in {chunk_count} chunks")
                        
                                                                    
                        await self._update_status(context, "Downloading file...", "Download complete, checking file...")
                        
                                                                               
                                                                      
                        await asyncio.sleep(0.1)
                        
                        actual_file_size = os.path.getsize(file_path)
                        print(f"File size on disk: {actual_file_size} bytes ({actual_file_size / (1024*1024):.2f}MB)")
                        
                                                                                      
                        if actual_file_size == 0 and total_downloaded > 0:
                            print(f"File size is 0 but we downloaded {total_downloaded} bytes - using downloaded amount")
                            actual_file_size = total_downloaded
                        
                        if actual_file_size == 0:
                            print("Downloaded file is empty - this might be a tunnel URL issue")
                                                                                                              
                            if "tunnel" in url or "api-cobalt" in url:
                                print("Attempting alternative tunnel download method...")
                                
                                                          
                                simple_headers = {'User-Agent': 'Mozilla/5.0 (compatible; bot)'}
                                async with session.get(url, headers=simple_headers, allow_redirects=True, timeout=aiohttp.ClientTimeout(total=60)) as retry_response:
                                    retry_total_downloaded = 0
                                    if retry_response.status == 200:
                                        print("Retry attempt - downloading with simpler headers...")
                                        async with aiofiles.open(file_path, 'wb') as f:
                                            async for chunk in retry_response.content.iter_chunked(8192):
                                                if chunk:
                                                    await f.write(chunk)
                                                    retry_total_downloaded += len(chunk)
                                        print(f"Retry download: {retry_total_downloaded} bytes")
                                        actual_file_size = os.path.getsize(file_path)
                                        print(f"Retry file size on disk: {actual_file_size} bytes")
                                    else:
                                        print(f"Retry failed with status: {retry_response.status}")
                                        retry_total_downloaded = 0

                                                                                 
                                if actual_file_size == 0:
                                    print("Range retry - requesting bytes=0- ...")
                                    range_headers = {
                                        'User-Agent': 'Mozilla/5.0 (compatible; bot)',
                                        'Range': 'bytes=0-',
                                        'Cache-Control': 'no-cache',
                                        'Pragma': 'no-cache',
                                    }
                                    async with session.get(url, headers=range_headers, allow_redirects=True, timeout=aiohttp.ClientTimeout(total=60)) as range_resp:
                                        range_downloaded = 0
                                        if range_resp.status in (200, 206):
                                            print(f"Range response status: {range_resp.status}")
                                            async with aiofiles.open(file_path, 'wb') as f:
                                                async for chunk in range_resp.content.iter_chunked(8192):
                                                    if chunk:
                                                        await f.write(chunk)
                                                        range_downloaded += len(chunk)
                                            print(f"Range retry downloaded: {range_downloaded} bytes")
                                            actual_file_size = os.path.getsize(file_path)
                                            print(f"Range retry file size on disk: {actual_file_size} bytes")
                                        else:
                                            print(f"Range retry failed with status: {range_resp.status}")

                                                                                                      
                                if actual_file_size == 0:
                                    print("Basic HTTP retry...")
                                    basic_headers = {
                                        'User-Agent': 'curl/7.68.0',
                                        'Accept': '*/*',
                                        'Connection': 'close'
                                    }
                                    basic_connector = aiohttp.TCPConnector(force_close=True)
                                    async with aiohttp.ClientSession(connector=basic_connector) as basic_session:
                                        async with basic_session.get(url, headers=basic_headers, timeout=aiohttp.ClientTimeout(total=60)) as basic_resp:
                                            basic_downloaded = 0
                                            if basic_resp.status == 200:
                                                print(f"Basic HTTP response status: {basic_resp.status}")
                                                async with aiofiles.open(file_path, 'wb') as f:
                                                    async for chunk in basic_resp.content.iter_chunked(8192):
                                                        if chunk:
                                                            await f.write(chunk)
                                                            basic_downloaded += len(chunk)
                                                print(f"Basic HTTP retry downloaded: {basic_downloaded} bytes")
                                                actual_file_size = os.path.getsize(file_path)
                                                print(f"Basic HTTP retry file size on disk: {actual_file_size} bytes")
                                            else:
                                                print(f"Basic HTTP retry failed with status: {basic_resp.status}")

                                                                                    
                                if actual_file_size == 0:
                                    print("All tunnel retries produced 0 bytes - trying next API")
                                    return False                                 
                            else:
                                print("Non-tunnel URL returned empty file - may be invalid")
                                return False
                        
                                                                  
                                                                                                                 
                        max_discord_size = 10 * 1024 * 1024
                        try:
                            if getattr(context, 'guild', None):
                                max_discord_size = int(getattr(context.guild, 'filesize_limit', max_discord_size) or max_discord_size)
                        except Exception:
                            pass
                        
                                                                                  
                        print(
                            f"[GATE] Size check: file={actual_file_size}B ({actual_file_size / (1024*1024):.2f}MB), "
                            f"limit={max_discord_size}B ({max_discord_size / (1024*1024):.2f}MB), "
                            f"allow_discord={actual_file_size <= max_discord_size}"
                        )
                                                                                            
                        if not (actual_file_size <= max_discord_size):
                            print(f"File size ({actual_file_size / (1024*1024):.2f}MB) exceeds guild limit ({max_discord_size / (1024*1024):.2f}MB), uploading to uguu.se...")
                            await self._update_status(context, "Downloading file...", f"File too large for discord (limit {max_discord_size / (1024*1024):.2f}MB), uploading to uguu.se ({actual_file_size / (1024*1024):.2f}MB)...")

                            upload_url = await self._upload_to_uguu(file_path, filename, context)
                            print(f"uguu.se upload result: {upload_url}")

                            if upload_url:
                                                                                    
                                final_file_size = os.path.getsize(file_path)
                                if final_file_size != actual_file_size:
                                    print(f"File size changed from {actual_file_size} to {final_file_size}, updating...")
                                    actual_file_size = final_file_size

                                                            
                                embed = self._styled_embed(
                                    title="Download complete",
                                    color=0x57F287
                                )
                                disp_title, disp_quality = self._extract_title_and_quality(cobalt_data.get('filename', filename) if cobalt_data else filename)
                                file_info_lines = [
                                    f"Size: `{actual_file_size / (1024*1024):.2f} MB`",
                                    f"Filename: `{disp_title or filename}`"
                                ]
                                if disp_quality:
                                    file_info_lines.append(f"Quality: `{disp_quality}`")
                                embed.add_field(name="File info", value="\n".join(file_info_lines), inline=False)
                                embed.add_field(name="Post", value=f"[{site_name}]({original_url})", inline=False)
                                await self._send_response(context, content=upload_url)
                                await self._send_response(context, embed=embed)
                                return True
                            else:
                                                                                                    
                                embed = self._styled_embed(
                                    title="Upload service unavailable",
                                    description="The file upload service (uguu.se) is currently experiencing issues. Here's a direct download link:",
                                    color=0xF1C40F
                                )
                                disp_title, disp_quality = self._extract_title_and_quality(cobalt_data.get('filename', filename) if cobalt_data else filename)
                                file_info = f"**{disp_title or filename}**"
                                if disp_quality:
                                    file_info += f" • `{disp_quality}`"
                                file_info += f" • `{actual_file_size / (1024*1024):.1f} MB`"
                                
                                embed.add_field(name="File", value=file_info, inline=False)
                                embed.add_field(
                                    name="Direct download", 
                                    value=f"[Click here to download]({url})\n\n⚠️ **This link expires in ~30 minutes**", 
                                    inline=False
                                )
                                embed.add_field(name="Post", value=f"[{site_name}]({original_url})", inline=False)
                                await self._send_response(context, embed=embed)
                                return False

                                                                                  
                        embed = self._styled_embed(
                            title="Download complete",
                            color=0x57F287
                        )
                        disp_title, disp_quality = self._extract_title_and_quality(cobalt_data.get('filename', filename) if cobalt_data else filename)
                        file_info_lines = [
                            f"Size: `{actual_file_size / (1024*1024):.2f} MB`",
                            f"Filename: `{disp_title or filename}`"
                        ]
                        if disp_quality:
                            file_info_lines.append(f"Quality: `{disp_quality}`")
                        embed.add_field(
                            name="File info",
                            value="\n".join(file_info_lines),
                            inline=False
                        )
                        embed.add_field(
                            name="Post",
                            value=f"[{site_name}]({original_url})",
                            inline=False
                        )

                                                                         
                        try:
                            print("Attempting Discord upload...")
                            print(f"File path exists: {os.path.exists(file_path)}")
                            print(f"File size on disk: {os.path.getsize(file_path)} bytes")

                                                                          
                            verified_file_size = os.path.getsize(file_path)
                            if verified_file_size != actual_file_size:
                                print(f"File size mismatch! Expected {actual_file_size}, got {verified_file_size}")
                                actual_file_size = verified_file_size

                                                                                 
                            try:
                                print("Direct path upload attempt...")
                                discord_file = discord.File(file_path, filename=safe_filename)
                                await self._update_status(context, "Downloading file...", "Upload complete!")
                                await self._send_response(context, embed=embed, file=discord_file)
                                print("Successfully sent file to Discord via direct path")
                            except Exception as path_error:
                                print(f"Direct path upload failed: {path_error}")

                                                                                          
                                with open(file_path, 'rb') as f:
                                    file_content = f.read()
                                print(f"Read {len(file_content)} bytes into memory for fallback")

                                if len(file_content) == 0:
                                    print("ERROR: File content is empty after reading!")
                                    return False

                                                              
                                if len(file_content) != actual_file_size:
                                    actual_file_size = len(file_content)
                                    disp_title, disp_quality = self._extract_title_and_quality(cobalt_data.get('filename', filename) if cobalt_data else filename)
                                    file_info_lines = [
                                        f"Size: `{actual_file_size / (1024*1024):.2f} mb`",
                                        f"Filename: `{disp_title or filename}`"
                                    ]
                                    if disp_quality:
                                        file_info_lines.append(f"Quality: `{disp_quality}`")
                                    embed.set_field_at(0, name="File info", value="\n".join(file_info_lines), inline=False)
                                    print(f"Updated embed with corrected file size: {actual_file_size} bytes")

                                                            
                                try:
                                    print("BytesIO buffer upload attempt...")
                                    buffer = BytesIO(file_content)
                                    buffer.seek(0)
                                    discord_file = discord.File(fp=buffer, filename=safe_filename)
                                    await self._update_status(context, "Downloading file...", "Upload complete!")
                                    await self._send_response(context, embed=embed, file=discord_file)
                                    print("Successfully sent file to Discord via BytesIO")
                                    buffer.close()
                                except Exception as bytesio_error:
                                    print(f"BytesIO approach failed: {bytesio_error}")

                                                               
                                    try:
                                        print("BytesIO v2 upload attempt...")
                                        fresh_buffer = BytesIO()
                                        fresh_buffer.write(file_content)
                                        fresh_buffer.seek(0)
                                        discord_file = discord.File(fresh_buffer, filename=safe_filename)
                                        await self._update_status(context, "Downloading file...", "Upload complete!")
                                        await self._send_response(context, embed=embed, file=discord_file)
                                        print("Successfully sent file to Discord via fresh BytesIO")
                                    except Exception as fresh_buffer_error:
                                        print(f"Fresh BytesIO approach failed: {fresh_buffer_error}")

                                                               
                                        try:
                                            print("Temp file upload attempt...")
                                            import tempfile
                                            unique_suffix = f"_{int(time.time())}_{hash(file_content) % 10000}.mp4"
                                            temp_dir = tempfile.gettempdir()
                                            temp_path = os.path.join(temp_dir, f"discord_upload{unique_suffix}")
                                            with open(temp_path, 'wb') as temp_f:
                                                temp_f.write(file_content)
                                                temp_f.flush()
                                                os.fsync(temp_f.fileno())
                                            if not os.path.exists(temp_path):
                                                raise FileNotFoundError("Temp file was not created")
                                            discord_file = discord.File(temp_path, filename=(safe_filename or "media.mp4"))
                                            await self._update_status(context, "Downloading file...", "Upload complete!")
                                            await self._send_response(context, embed=embed, file=discord_file)
                                            print("Successfully sent file to Discord via temp file")
                                            await asyncio.sleep(1.0)
                                            try:
                                                if os.path.exists(temp_path):
                                                    os.remove(temp_path)
                                            except Exception as cleanup_error:
                                                print(f"Failed to cleanup temp file: {cleanup_error}")
                                        except Exception as temp_file_error:
                                            print(f"Temp file approach failed: {temp_file_error}")

                                                                                           
                                            try:
                                                print("ASCII filename path upload attempt...")
                                                base_for_ascii = safe_filename or filename
                                                ascii_filename = "".join(c for c in base_for_ascii if ord(c) < 128 and (c.isalnum() or c in (' ', '.', '_', '-')))
                                                if not ascii_filename or not ascii_filename.strip():
                                                    ascii_filename = base_for_ascii if base_for_ascii else "downloaded_media.mp4"
                                                discord_file = discord.File(file_path, filename=ascii_filename)
                                                await self._update_status(context, "Downloading file...", "Upload complete!")
                                                await self._send_response(context, embed=embed, file=discord_file)
                                                print("Successfully sent file to Discord via ASCII filename path")
                                            except Exception as final_error:
                                                print(f"All upload methods failed. Final error: {final_error}")
                                                raise final_error
                            
                            print("Discord Upload completed successfully")
                            
                                                                                        
                                                                       
                            try:
                                if os.path.exists(file_path):
                                    os.remove(file_path)
                                    print(f"Cleaned up original file: {file_path}")
                            except Exception as cleanup_error:
                                print(f"Failed to delete original file: {cleanup_error}")
                            
                            return True           
                            
                        except Exception as discord_error:
                            print(f"Discord upload failed with error: {discord_error}")
                                                                         
                            upload_url = await self._upload_to_uguu(file_path, filename, context)
                            if upload_url:
                                                                           
                                final_file_size = os.path.getsize(file_path)
                                if final_file_size != actual_file_size:
                                    print(f"File size changed from {actual_file_size} to {final_file_size}, updating...")
                                    actual_file_size = final_file_size
                                
                                embed.clear_fields()
                                                                  
                                disp_title, disp_quality = self._extract_title_and_quality(cobalt_data.get('filename', filename) if cobalt_data else filename)
                                file_info_lines = [
                                    f"Size: `{actual_file_size / (1024*1024):.2f} mb`",
                                    f"Filename: `{disp_title or filename}`"
                                ]
                                if disp_quality:
                                    file_info_lines.append(f"Quality: `{disp_quality}`")
                                embed.add_field(
                                    name="File info",
                                    value="\n".join(file_info_lines),
                                    inline=False
                                )
                                if cobalt_data and not is_audio:
                                    cobalt_filename = cobalt_data.get('filename', '')
                                    if cobalt_filename:
                                        parsed_metadata = self._parse_youtube_filename(cobalt_filename)
                                        if parsed_metadata:
                                            metadata_text = ""
                                            if parsed_metadata.get('title'):
                                                metadata_text += f"Title: `{parsed_metadata['title']}`\n"
                                            if parsed_metadata.get('artist'):
                                                metadata_text += f"Artist: `{parsed_metadata['artist']}`\n"
                                            if parsed_metadata.get('channel'):
                                                metadata_text += f"Channel: `{parsed_metadata['channel']}`\n"
                                            if parsed_metadata.get('additional_info'):
                                                metadata_text += f"Info: `{parsed_metadata['additional_info']}`\n"
                                            if metadata_text:
                                                embed.add_field(
                                                    name="Media info",
                                                    value=metadata_text.strip(),
                                                    inline=False
                                                )
                                embed.add_field(
                                    name="Post",
                                    value=f"[{site_name}]({original_url})",
                                    inline=False
                                )
                                                                            
                                await self._send_response(context, content=upload_url)
                                                     
                                await self._send_response(context, embed=embed)
                                return True                                 
                            else:
                                                   
                                embed = self._styled_embed(
                                    title="Upload failed",
                                    description="Failed to upload file to Discord and uguu.se. Providing direct download link instead.",
                                    color=0xF1C40F
                                )
                                embed.add_field(
                                    name="Direct download",
                                    value=f"[click here to download]({url})",
                                    inline=False
                                )
                                embed.add_field(
                                    name="Post",
                                    value=f"[{site_name}]({original_url})",
                                    inline=False
                                )
                                await self._send_response(context, embed=embed)
                                return False                                   
                        else:
                                                                               
                            print(f"File size ({actual_file_size / (1024*1024):.2f}MB) exceeds guild limit ({max_discord_size / (1024*1024):.2f}MB), uploading to uguu.se...")
                            await self._update_status(context, "Downloading file...", f"File too large for discord (limit {max_discord_size / (1024*1024):.2f}MB), uploading to uguu.se ({actual_file_size / (1024*1024):.2f}MB)...")
                            
                            upload_url = await self._upload_to_uguu(file_path, filename, context)
                            print(f"uguu.se upload result: {upload_url}")
                            
                            if upload_url:
                                print("uguu.se upload successful, creating metadata embed")
                                
                                                                                    
                                final_file_size = os.path.getsize(file_path)
                                if final_file_size != actual_file_size:
                                    print(f"File size changed from {actual_file_size} to {final_file_size}, updating...")
                                    actual_file_size = final_file_size
                                
                                                            
                                embed = self._styled_embed(
                                    title="Download complete",
                                    color=0x57F287
                                )

                                                                  
                                disp_title, disp_quality = self._extract_title_and_quality(cobalt_data.get('filename', filename) if cobalt_data else filename)
                                file_info_lines = [
                                    f"Size: `{actual_file_size / (1024*1024):.2f} MB`",
                                    f"Filename: `{disp_title or filename}`"
                                ]
                                if disp_quality:
                                    file_info_lines.append(f"Quality: `{disp_quality}`")
                                embed.add_field(
                                    name="File info",
                                    value="\n".join(file_info_lines),
                                    inline=False
                                )

                                                                       
                                actual_quality = None
                                requested_quality = None
                                fallback_used = False
                                if cobalt_data and not is_audio:
                                    cobalt_filename = cobalt_data.get('filename', '')
                                    print(f"Parsing filename: {cobalt_filename}")

                                                                                                       
                                    if cobalt_filename:
                                        parsed_metadata = self._parse_youtube_filename(cobalt_filename)
                                                                                                      
                                        if '(' in cobalt_filename and ')' in cobalt_filename:
                                            paren = cobalt_filename[cobalt_filename.rfind('(')+1 : cobalt_filename.rfind(')')]
                                                                   
                                            import re
                                            match = re.search(r'(\d{3,4}p)', paren)
                                            if match:
                                                actual_quality = match.group(1)
                                                                                                
                                                                                               
                                        if hasattr(context, 'remorse_requested_quality'):
                                            requested_quality = context.remorse_requested_quality
                                        elif hasattr(context, 'command_quality'):
                                            requested_quality = context.command_quality
                                                                                          
                                        if not requested_quality:
                                            requested_quality = cobalt_data.get('requested_quality')
                                                                                             
                                        if not requested_quality and hasattr(context, 'message'):
                                            import re
                                            m = re.search(r'remorse\s+\S+\s+(\d{3,4}p?)', context.message.content)
                                            if m:
                                                requested_quality = m.group(1)
                                                if not requested_quality.endswith('p'):
                                                    requested_quality += 'p'
                                                        
                                        if requested_quality and not requested_quality.endswith('p'):
                                            requested_quality += 'p'
                                        if actual_quality and not actual_quality.endswith('p'):
                                            actual_quality += 'p'
                                                                       
                                        if requested_quality and actual_quality and requested_quality != actual_quality:
                                            fallback_used = True
                                        metadata_text = ""
                                        if parsed_metadata:
                                            if parsed_metadata.get('title'):
                                                metadata_text += f"Title: `{parsed_metadata['title']}`\n"
                                            if parsed_metadata.get('artist'):
                                                metadata_text += f"Artist: `{parsed_metadata['artist']}`\n"
                                            if parsed_metadata.get('channel'):
                                                metadata_text += f"Channel: `{parsed_metadata['channel']}`\n"
                                            if parsed_metadata.get('additional_info'):
                                                metadata_text += f"Info: `{parsed_metadata['additional_info']}`\n"
                                        if metadata_text:
                                            embed.add_field(
                                                name="Media info",
                                                value=metadata_text.strip(),
                                                inline=False
                                            )
                                                                  
                                if fallback_used and requested_quality and actual_quality:
                                    embed.add_field(
                                        name="Quality warning",
                                        value=f"⚠️ Requested **{requested_quality}**, but downloaded **{actual_quality}**. The requested quality may not have been available.",
                                        inline=False
                                    )

                        embed.add_field(
                            name="Post",
                            value=f"[{site_name}]({original_url})",
                            inline=False
                        )

                                                            
                        await self._update_status(context, "Downloading file...", "uguu.se upload complete!")

                                                                                
                        await self._send_response(context, content=upload_url)

                                                           
                        await self._send_response(context, embed=embed)
                        return True                        
                    else:
                        print("uguu.se upload failed, creating fallback embed")
                                                          
                        embed = self._styled_embed(
                            title="Upload failed",
                            description="Failed to upload file to uguu.se. Providing direct download link instead.",
                            color=0xF1C40F
                        )
                        embed.add_field(
                            name="Direct download",
                            value=f"[click here to download]({url})",
                            inline=False
                        )
                        embed.add_field(
                            name="Post",
                            value=f"[{site_name}]({original_url})",
                            inline=False
                        )
                        await self._send_response(context, embed=embed)
                        return False                        
                    
                    if response.status in (302, 301):
                                                   
                        redirect_url = response.headers.get('Location')
                        if redirect_url:
                            print(f"Following redirect to: {redirect_url}")
                            return await self._download_and_send_file(
                                context, redirect_url, filename, original_url, is_audio, cobalt_data, show_status=False
                            )
                        else:
                            print(f"Received redirect status {response.status} but no Location header")
                            return False                
                
                                               
                                                         
                    response_text = await response.text()
                    print(f"Download failed with status {response.status}")
                    print(f"Response body: {response_text[:500]}...")                   
                    
                                                                              
                    if (response.status == 403 and "signature mismatch" in response_text.lower() and 
                        "instagram.com" in original_url):
                        print("Instagram signature mismatch detected - providing direct Instagram link")
                        embed = self._styled_embed(
                            title="Instagram download",
                            description="Instagram links expire quickly. Here's the direct link:",
                            color=0xF1C40F
                        )
                        embed.add_field(
                            name="Direct Instagram link",
                            value=f"[View on Instagram]({original_url})",
                            inline=False
                        )
                        embed.add_field(
                            name="Tip",
                            value="Try saving the video directly from Instagram or screenshot for images",
                            inline=False
                        )
                        await self._send_response(context, embed=embed)
                        return True                                                       
                    
                    embed = self._styled_embed(
                        title="Download failed",
                        description=f"Failed to download file (status: {response.status})",
                        color=0xE02B2B
                    )
                    embed.add_field(
                        name="Direct download",
                        value=f"[click here to download]({url})",
                        inline=False
                    )
                    embed.add_field(
                        name="Post",
                        value=f"[{site_name}]({original_url})",
                        inline=False
                    )
                    await self._send_response(context, embed=embed)
                    return False                
        
        except asyncio.TimeoutError:
            print("Download timeout - will try next API if available")
            return False                
            
        except Exception as e:
            print(f"Download error: {e} - will try next API if available")
            return False                
        
        finally:
                                                       
            if file_path and os.path.exists(file_path):
                try:
                    os.remove(file_path)
                    print(f"Cleaned up remaining file: {file_path}")
                except Exception as e:
                    print(f"Failed to delete remaining file {file_path}: {e}")

    def _parse_youtube_filename(self, filename: str) -> dict:
        """
        Parse metadata from YouTube filename formats returned by Cobalt.
        
        Expected formats:
        - "Artist - Title - Channel (quality, codec, platform).ext"
        - "Artist - Title [Additional Info] - Channel (quality, codec, platform).ext"
        - "Title - Channel (quality, codec, platform).ext"
        
        Returns:
            dict: Parsed metadata with keys: title, artist, channel, additional_info
        """
        try:
            if not filename:
                return {}
            
                                   
            name_without_ext = filename
            if '.' in filename:
                name_without_ext = filename.rsplit('.', 1)[0]
            
                                                                
            if ' (' in name_without_ext and name_without_ext.count('(') >= 1:
                                                                                        
                last_paren_index = name_without_ext.rfind(' (')
                clean_name = name_without_ext[:last_paren_index]
            else:
                clean_name = name_without_ext
            
            print(f"Clean name after processing: '{clean_name}'")
            
                                                   
            parts = [part.strip() for part in clean_name.split(' - ')]
            print(f"Split parts: {parts}")
            
            metadata = {}
            
            if len(parts) >= 3:
                                                                                         
                artist = parts[0]
                title_part = parts[1]
                channel = parts[2]
                
                                                                
                if '[' in title_part and ']' in title_part:
                                                       
                    bracket_start = title_part.find('[')
                    bracket_end = title_part.find(']')
                    title = title_part[:bracket_start].strip()
                    additional_info = title_part[bracket_start+1:bracket_end].strip()
                    
                    metadata['title'] = title
                    metadata['additional_info'] = additional_info
                else:
                    metadata['title'] = title_part
                
                metadata['artist'] = artist
                metadata['channel'] = channel
                
            elif len(parts) == 2:
                                                               
                                                                 
                metadata['title'] = parts[0]
                metadata['channel'] = parts[1]
                
            elif len(parts) == 1:
                                               
                metadata['title'] = parts[0]
            
            print(f"Parsed metadata: {metadata}")
            return metadata
            
        except Exception as e:
            print(f"Error parsing filename '{filename}': {e}")
            return {}

    def _extract_title_and_quality(self, filename: str) -> tuple[str | None, str | None]:
        """Return a clean title and quality (e.g., 1080p) from a Cobalt filename.
        Falls back gracefully when structure is unexpected.
        """
        try:
            if not filename:
                return None, None

                              
            base = filename.rsplit('.', 1)[0]

                                                                                     
            quality = None
            m = re.search(r"\(([^)]*)\)\s*$", base)
            if m:
                details = m.group(1)
                mq = re.search(r"(\d{3,4}p)", details, re.IGNORECASE)
                if mq:
                    quality = mq.group(1).lower()

                                                                 
            base_no_paren = base[: base.rfind(" (")] if " (" in base else base

                                                        
            md = self._parse_youtube_filename(filename)
            title = (md or {}).get("title")
            if not title:
                                                                                          
                title = base_no_paren.split(" - ")[0].strip() if " - " in base_no_paren else base_no_paren.strip()

                                               
            if title and title.startswith('"') and title.endswith('"'):
                title = title[1:-1].strip()

            return title or None, quality
        except Exception:
            return None, None

    async def _upload_to_uguu(self, file_path: str, filename: str, context: Context) -> Optional[str]:
        """
        Upload a file to uguu.se and return the URL.
        
        Args:
            file_path: Path to the file to upload
            filename: Name for the uploaded file
            context: Discord context for status updates
            
        Returns:
            URL of uploaded file, or None if upload failed
        """
        try:
                                                                  
            file_size = os.path.getsize(file_path)
            max_size = 128 * 1024 * 1024                  
            
            if file_size > max_size:
                print(f"File too large for uguu.se: {file_size / (1024*1024):.2f}MB (max 128MB)")
                return None
            
            print(f"Uploading {file_size / (1024*1024):.2f}MB to uguu.se...")
            await self._update_status(context, "Uploading to uguu.se...", f"Uploading {file_size / (1024*1024):.2f}MB to uguu.se (free CDN)...")
            
            async with aiofiles.open(file_path, 'rb') as f:
                file_data = await f.read()
            
                                  
            url = 'https://uguu.se/upload.php'
            
                              
            data = aiohttp.FormData()
            data.add_field('files[]', file_data, filename=filename, content_type='application/octet-stream')
            
                                 
            timeout = aiohttp.ClientTimeout(total=300)                    
            session = await self.get_session()
            async with session.post(url, data=data, timeout=timeout) as response:
                    print(f"uguu.se response status: {response.status}")
                    
                    if response.status != 200:
                        print(f"uguu.se upload failed with status {response.status}")
                        response_text = await response.text()
                        print(f"Response: {response_text[:500]}")
                        return None
                    
                                                                              
                    response_json = await response.json()
                    print(f"uguu.se response: {response_json}")
                    
                    if response_json.get('success') and response_json.get('files'):
                        upload_url = response_json['files'][0].get('url')
                        if upload_url:
                            print(f"Successfully uploaded to uguu.se: {upload_url}")
                            return upload_url
                    
                    print("uguu.se response missing expected fields")
                    return None
                    
        except asyncio.TimeoutError:
            print("uguu.se upload timed out")
            return None
        except Exception as e:
            print(f"Error uploading to uguu.se: {e}")
            import traceback
            traceback.print_exc()
            return None

    async def _update_status(self, context: Context, title: str, description: str) -> None:
        """Update the status message with new progress information."""
        try:
            if hasattr(context, 'status_message') and context.status_message:
                embed = self._styled_embed(title=title, description=description, show_footer=False)
                await context.status_message.edit(embed=embed)
        except Exception as e:
            print(f"Failed to update status message: {e}")

    def _get_site_name(self, url: str) -> str:
        """Extracts a friendly site name from a URL."""
        domain_map = {
            "youtube.com": "YouTube",
            "youtu.be": "YouTube",
            "tiktok.com": "TikTok",
            "twitter.com": "Twitter",
            "x.com": "Twitter",
            "instagram.com": "Instagram",
            "instagram.embedez.com": "Instagram",
            "reddit.com": "Reddit",
            "facebook.com": "Facebook",
            "soundcloud.com": "SoundCloud",
            "vimeo.com": "Vimeo",
            "twitch.tv": "Twitch",
            "bilibili.com": "Bilibili",
            "dailymotion.com": "Dailymotion",
            "rumble.com": "Rumble",
            "bitchute.com": "BitChute",
            "odysee.com": "Odysee",
            "kick.com": "Kick",
            "streamable.com": "Streamable",
            "imgur.com": "Imgur",
            "uguu.se": "uguu.se",
        }
        try:
            match = re.search(r"https?://(?:www\.)?([^/]+)", url)
            if match:
                domain = match.group(1).lower()
                for key, name in domain_map.items():
                    if key in domain:
                        return name
                                                  
                return domain.split('.')[0].capitalize()
        except Exception:
            pass
        return "media"

async def setup(bot) -> None:
    await bot.add_cog(Media(bot))