import re

with open('static/index.html', 'r', encoding='utf-8') as f:
    content = f.read()

# Remove all emojis
# A simplistic regex for common emojis in this project:
emojis = ["🔥", "💡", "🎵", "🔄", "✏️", "🗑️", "🎨", "☀", "🎭", "🌈", "🌋", "🎤", "🥁", "🎻", "⚡", "💖", "🌌", "🌠", "🌊", "🍯", "🚥", "🪩", "🎧", "✨", "🤖", "🚀", "❤️", "🌡️", "❄️", "🔔", "⏰", "⏳", "🍅", "☕"]
for e in emojis:
    content = content.replace(e + " ", "")
    content = content.replace(e, "")

# Remove AI tab button
content = re.sub(r'<button class="sync-tab" data-sync="ai" id="tab-btn-ai">.*?AI Vibe</button>\n?', '', content)

# Remove AI sync view
content = re.sub(r'<div id="sync-view-ai" class="sync-view hidden">.*?</div><!-- /ai-sync-view -->\n?', '', content, flags=re.DOTALL)

with open('static/index.html', 'w', encoding='utf-8') as f:
    f.write(content)

print("Done patching index.html")
