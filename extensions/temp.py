from interactions import Extension, Client, listen
import json
class Temp(Extension):
    def __init__(self, bot):
        self.bot : Client = bot
    
    @listen()
    async def on_ready(self):
        # Download all the message from the channel and make a json file with the content and the author
        channel = await self.bot.fetch_channel(1282407006241820794)
        messages = []
        async for message in channel.history(limit=100):
            messages.append({
                'content': message.content,
                'author': message.author.username,
            })
        with open('data/messages.json', 'w') as f:
            json.dump(messages, f)
        print('Done')
        
        
        