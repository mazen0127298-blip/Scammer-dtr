try:
    import discord
except ImportError:
    import sys, subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "discord.py>=2.3.2"])
    import discord

import os
import json
import asyncio
from typing import Optional, List, Tuple
from discord import app_commands
from discord.ext import commands

INTENTS = discord.Intents.default()
INTENTS.guilds = True
INTENTS.members = True
INTENTS.message_content = False

BOT = commands.Bot(command_prefix="!", intents=INTENTS)
TREE = BOT.tree

DATA_DIR = "data"
FILES = {
    "config": os.path.join(DATA_DIR, "config.json"),
    "buttons": os.path.join(DATA_DIR, "buttons.json"),
    "tickets": os.path.join(DATA_DIR, "tickets.json"),
    "categories": os.path.join(DATA_DIR, "categories.json"),
}

DEFAULTS = {
    "config": {
        "guild_id": None,
        "mention_role_id": None,
        "write_role_id": None,
        "in_ticket_buttons": {
            "close_label": "قفل التذكرة",
            "accept_label": "استلام التذكرة"
        },
        "receipt_message": "✅ تم استلام هذه التذكرة. سيتم التعامل معها قريبًا."
    },
    "buttons": {},
    "tickets": {},
    "categories": {}
}

COLOR_TO_STYLE = {
    "green": discord.ButtonStyle.success,
    "red": discord.ButtonStyle.danger,
    "blue": discord.ButtonStyle.primary,
    "gray": discord.ButtonStyle.secondary,
    "white": discord.ButtonStyle.secondary,
}

def ensure_files():
    os.makedirs(DATA_DIR, exist_ok=True)
    for key, path in FILES.items():
        if not os.path.exists(path):
            with open(path, "w", encoding="utf-8") as f:
                json.dump(DEFAULTS[key], f, ensure_ascii=False, indent=2)

def load_json(name: str):
    with open(FILES[name], "r", encoding="utf-8") as f:
        return json.load(f)

def save_json(name: str, data):
    with open(FILES[name], "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def style_from_text(txt: str) -> discord.ButtonStyle:
    return COLOR_TO_STYLE.get(txt.lower(), discord.ButtonStyle.secondary)

def can_use_admin_commands(interaction: discord.Interaction, ticket_info: dict) -> bool:
    if interaction.user.guild_permissions.manage_channels:
        return True
    handler_id = ticket_info.get("handler_id")
    if handler_id and interaction.user.id == handler_id:
        return True
    return False

async def set_ticket_permissions(channel: discord.TextChannel,
                                 owner: discord.Member,
                                 handler: Optional[discord.Member],
                                 write_role: Optional[discord.Role],
                                 locked: bool):
    overwrites = channel.overwrites
    guild = channel.guild
    overwrites[guild.default_role] = discord.PermissionOverwrite(send_messages=not locked, read_messages=True, view_channel=True)
    overwrites[owner] = discord.PermissionOverwrite(send_messages=True, read_messages=True, view_channel=True)
    if handler:
        overwrites[handler] = discord.PermissionOverwrite(send_messages=True, read_messages=True, view_channel=True)
    if write_role:
        overwrites[write_role] = discord.PermissionOverwrite(send_messages=True, read_messages=True, view_channel=True)
    await channel.edit(overwrites=overwrites, reason="Ticket permissions update")

class OpenTicketButton(discord.ui.Button):
    def __init__(self, label: str, style_text: str, category_id: int):
        super().__init__(label=label, style=style_from_text(style_text), custom_id=f"open_ticket:{category_id}:{label}")
        self.category_id = category_id
        self.style_text = style_text

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=False)
        config = load_json("config")
        mention_role_id = config.get("mention_role_id")
        write_role_id = config.get("write_role_id")
        write_role = interaction.guild.get_role(write_role_id) if write_role_id else None
        cat = interaction.guild.get_channel(self.category_id)
        if not isinstance(cat, discord.CategoryChannel):
            return await interaction.followup.send("❌ الكاتيجوري المحددة غير موجودة.", ephemeral=True)
        base_name = f"ticket-{interaction.user.name}".replace(" ", "-")
        name = base_name[:85]
        channel = await interaction.guild.create_text_channel(name=name, category=cat, reason="فتح تذكرة جديدة")
        tickets = load_json("tickets")
        tickets[str(channel.id)] = {"owner_id": interaction.user.id, "handler_id": None}
        save_json("tickets", tickets)
        await set_ticket_permissions(channel, interaction.user, None, write_role, locked=False)
        auto = load_json("categories").get(str(self.category_id), {}).get("auto_message")
        embed = discord.Embed(
            title="🎫 تذكرة جديدة",
            description=f"مرحبًا {interaction.user.mention}! تم فتح تذكرتك بنجاح.\nالغرض: **{self.label}**",
            color=discord.Color.blurple()
        )
        embed.set_footer(text=f"مُنشأة عبر الزر: {self.label}")
        if auto:
            if auto.get("text"):
                embed.add_field(name="الرسالة التلقائية", value=auto["text"][:1024], inline=False)
            if auto.get("image"):
                embed.set_image(url=auto["image"])
        view = InTicketControlsView()
        await channel.send(embed=embed, view=view)
        if mention_role_id:
            role = interaction.guild.get_role(mention_role_id)
            if role:
                try:
                    await channel.send(role.mention)
                except discord.Forbidden:
                    pass
        await interaction.followup.send(f"✅ تم فتح التذكرة: {channel.mention}", ephemeral=True)

class OpenButtonsView(discord.ui.View):
    def __init__(self, mapping: List[Tuple[str, str, int]], timeout=None):
        super().__init__(timeout=timeout)
        for label, color, cat_id in mapping:
            self.add_item(OpenTicketButton(label, color, cat_id))

class AcceptTicketButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label=None, style=discord.ButtonStyle.success, custom_id="ticket_accept")

    async def callback(self, interaction: discord.Interaction):
        channel = interaction.channel
        tickets = load_json("tickets")
        info = tickets.get(str(channel.id))
        if not info:
            return await interaction.response.send_message("❌ هذه القناة ليست تذكرة.", ephemeral=True)
        if interaction.user.id == info["owner_id"]:
            return await interaction.response.send_message("❌ لا يمكنك استلام هذه التذكرة لأنك صاحبها.", ephemeral=True)
        config = load_json("config")
        write_role_id = config.get("write_role_id")
        write_role = interaction.guild.get_role(write_role_id) if write_role_id else None
        owner = interaction.guild.get_member(info["owner_id"])
        handler = interaction.user
        info["handler_id"] = handler.id
        save_json("tickets", tickets)
        await set_ticket_permissions(channel, owner, handler, write_role, locked=True)
        receipt = config.get("receipt_message") or "✅ تم الاستلام."
        embed = discord.Embed(title="📩 تم استلام التذكرة", description=receipt, color=discord.Color.green())
        await interaction.response.send_message(embed=embed)

class CloseTicketButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label=None, style=discord.ButtonStyle.danger, custom_id="ticket_close")

    async def callback(self, interaction: discord.Interaction):
        tickets = load_json("tickets")
        info = tickets.get(str(interaction.channel.id))
        if not info:
            return await interaction.response.send_message("❌ هذه القناة ليست تذكرة.", ephemeral=True)
        if interaction.user.id == info["owner_id"]:
            return await interaction.response.send_message("❌ لا يمكنك إغلاق التذكرة. فقط المسؤول/المستلم.", ephemeral=True)
        if not (interaction.user.guild_permissions.manage_channels or info.get("handler_id") == interaction.user.id):
            return await interaction.response.send_message("❌ لا تملك صلاحية إغلاق هذه التذكرة.", ephemeral=True)
        await interaction.response.send_message("🗑️ سيتم إغلاق التذكرة خلال 3 ثوانٍ...")
        await asyncio.sleep(3)
        try:
            del tickets[str(interaction.channel.id)]
            save_json("tickets", tickets)
        except KeyError:
            pass
        await interaction.channel.delete(reason="إغلاق التذكرة")

class InTicketControlsView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        cfg = load_json("config")
        close_label = cfg["in_ticket_buttons"]["close_label"]
        accept_label = cfg["in_ticket_buttons"]["accept_label"]
        accept_btn = AcceptTicketButton()
        accept_btn.label = accept_label
        close_btn = CloseTicketButton()
        close_btn.label = close_label
        self.add_item(accept_btn)
        self.add_item(close_btn)

async def rebuild_persistent_views():
    buttons = load_json("buttons")
    for _, btns in buttons.items():
        mapping = [(b["label"], b["style"], int(b["category_id"])) for b in btns]
        BOT.add_view(OpenButtonsView(mapping, timeout=None))

@BOT.event
async def on_ready():
    ensure_files()
    await rebuild_persistent_views()
    try:
        synced = await TREE.sync()
        print(f"Synced {len(synced)} app commands.")
    except Exception as e:
        print("Sync error:", e)
    print(f"Logged in as {BOT.user} (ID: {BOT.user.id})")

@TREE.command(name="role-manshen", description="تحديد رتبة يتم منشنها عند فتح تذكرة")
@app_commands.describe(role="اختر الرتبة")
async def role_manshen(interaction: discord.Interaction, role: discord.Role):
    config = load_json("config")
    config["mention_role_id"] = role.id
    save_json("config", config)
    await interaction.response.send_message(f"✅ سيتم منشن {role.mention} عند فتح أي تذكرة.", ephemeral=True)

@TREE.command(name="write-in-ticket", description="تحديد رتبة مسموح لها الكتابة دائمًا في أي تذكرة")
@app_commands.describe(role="اختر الرتبة")
async def write_in_ticket(interaction: discord.Interaction, role: discord.Role):
    config = load_json("config")
    config["write_role_id"] = role.id
    save_json("config", config)
    await interaction.response.send_message(f"✅ رتبة {role.mention} يمكنها الكتابة دائمًا داخل التذاكر.", ephemeral=True)

@TREE.command(name="message-receipt", description="تحديد رسالة تظهر عند استلام التذكرة (الزر الأخضر)")
@app_commands.describe(text="نص رسالة الاستلام")
async def message_receipt(interaction: discord.Interaction, text: app_commands.Range[str, 1, 1024]):
    config = load_json("config")
    config["receipt_message"] = text
    save_json("config", config)
    await interaction.response.send_message("✅ تم تحديث رسالة الاستلام.", ephemeral=True)

@TREE.command(name="name-button-ticket", description="تغيير أسماء الأزرار داخل التذكرة (استلام/قفل)")
@app_commands.describe(accept_label="اسم زر الاستلام (الأخضر)", close_label="اسم زر القفل (الأحمر)")
async def name_button_ticket(interaction: discord.Interaction,
                             accept_label: app_commands.Range[str, 1, 80],
                             close_label: app_commands.Range[str, 1, 80]):
    config = load_json("config")
    config["in_ticket_buttons"]["accept_label"] = accept_label
    config["in_ticket_buttons"]["close_label"] = close_label
    save_json("config", config)
    await interaction.response.send_message("✅ تم تحديث أسماء الأزرار داخل التذكرة.", ephemeral=True)

@TREE.command(name="message-ticket", description="ضبط/حذف الرسالة التلقائية عند فتح تذكرة في كاتيجوري معين")
@app_commands.describe(category="اختر الكاتيجوري", text="نص الرسالة (اختياري)", image_url="رابط صورة (اختياري)", delete="حذف الرسالة التلقائية؟")
async def message_ticket(interaction: discord.Interaction,
                         category: discord.CategoryChannel,
                         text: Optional[app_commands.Range[str, 1, 1024]] = None,
                         image_url: Optional[str] = None,
                         delete: Optional[bool] = False):
    categories = load_json("categories")
    key = str(category.id)
    if delete:
        if key in categories:
            categories[key].pop("auto_message", None)
            if not categories[key]:
                categories.pop(key, None)
        save_json("categories", categories)
        return await interaction.response.send_message("🗑️ تم حذف الرسالة التلقائية لهذه الكاتيجوري.", ephemeral=True)
    categories.setdefault(key, {})
    categories[key]["auto_message"] = {"text": text or "", "image": image_url or None}
    save_json("categories", categories)
    await interaction.response.send_message("✅ تم حفظ الرسالة التلقائية لهذه الكاتيجوري.", ephemeral=True)

@TREE.command(name="new-ticket", description="إرسال رسالة مع زر/أزرار فتح تذكرة")
@app_commands.describe(
    message="النص الذي سيظهر في الإيمبد",
    button_label="اسم الزر (مثال: الدعم الفني)",
    category="الكاتيجوري الذي ستفتح فيه التذكرة",
    button_color="لون الزر: green/red/blue/gray/white"
)
async def new_ticket(interaction: discord.Interaction,
                     message: app_commands.Range[str, 1, 1024],
                     button_label: app_commands.Range[str, 1, 80],
                     category: discord.CategoryChannel,
                     button_color: app_commands.Choice[str] = app_commands.Choice(name="green", value="green")):
    await interaction.response.defer(ephemeral=True)
    embed = discord.Embed(title="📨 فتح تذكرة", description=message, color=discord.Color.blurple())
    embed.set_footer(text=f"بواسطة: {interaction.user.display_name}")
    mapping = [(button_label, button_color.value, category.id)]
    view = OpenButtonsView(mapping, timeout=None)
    msg = await interaction.channel.send(embed=embed, view=view)
    buttons = load_json("buttons")
    buttons[str(msg.id)] = [{"label": button_label, "style": button_color.value, "category_id": category.id}]
    save_json("buttons", buttons)
    await interaction.followup.send("✅ تم إرسال رسالة فتح التذكرة.", ephemeral=True)

@new_ticket.autocomplete("button_color")
async def color_autocomplete(interaction: discord.Interaction, current: str):
    options = ["green", "red", "blue", "gray", "white"]
    return [app_commands.Choice(name=o, value=o) for o in options if current.lower() in o][:5]

@TREE.command(name="add-button-ticket", description="إضافة زر فتح تذكرة لرسالة موجودة")
@app_commands.describe(
    message_id="ID الرسالة التي تريد إضافة زر لها",
    button_label="اسم الزر الجديد",
    category="الكاتيجوري الذي ستفتح فيه التذكرة",
    button_color="لون الزر: green/red/blue/gray/white"
)
async def add_button_ticket(interaction: discord.Interaction,
                            message_id: app_commands.Range[str, 1, 30],
                            button_label: app_commands.Range[str, 1, 80],
                            category: discord.CategoryChannel,
                            button_color: app_commands.Choice[str] = app_commands.Choice(name="blue", value="blue")):
    await interaction.response.defer(ephemeral=True)
    buttons = load_json("buttons")
    if message_id not in buttons:
        return await interaction.followup.send("❌ لم أجد تعريف أزرار لهذه الرسالة. تأكد من ID.", ephemeral=True)
    try:
        msg = await interaction.channel.fetch_message(int(message_id))
    except Exception:
        return await interaction.followup.send("❌ لم أستطع جلب الرسالة. تأكد أنك في نفس القناة أو أعطني صلاحيات.", ephemeral=True)
    btn_list = buttons[message_id]
    btn_list.append({"label": button_label, "style": button_color.value, "category_id": category.id})
    save_json("buttons", buttons)
    mapping = [(b["label"], b["style"], int(b["category_id"])) for b in btn_list]
    view = OpenButtonsView(mapping, timeout=None)
    await msg.edit(view=view)
    await interaction.followup.send("✅ تم إضافة الزر إلى الرسالة.", ephemeral=True)

@TREE.command(name="rename", description="تغيير اسم قناة التذكرة")
@app_commands.describe(name="الاسم الجديد")
async def rename_ticket(interaction: discord.Interaction, name: app_commands.Range[str, 1, 90]):
    tickets = load_json("tickets")
    info = tickets.get(str(interaction.channel.id))
    if not info:
        return await interaction.response.send_message("❌ هذه ليست قناة تذكرة.", ephemeral=True)
    if interaction.user.id == info["owner_id"]:
        return await interaction.response.send_message("❌ لا يمكنك استعمال هذا الأمر. فقط المسؤول عن التذكرة.", ephemeral=True)
    if not can_use_admin_commands(interaction, info):
        return await interaction.response.send_message("❌ ليس لديك صلاحية لإعادة التسمية.", ephemeral=True)
    await interaction.channel.edit(name=name, reason="إعادة تسمية التذكرة")
    await interaction.response.send_message(f"✏️ تم تغيير الاسم إلى `{name}`.", ephemeral=True)

@TREE.command(name="close", description="إغلاق (حذف) قناة التذكرة الحالية")
async def close_ticket_cmd(interaction: discord.Interaction):
    tickets = load_json("tickets")
    info = tickets.get(str(interaction.channel.id))
    if not info:
        return await interaction.response.send_message("❌ هذه ليست قناة تذكرة.", ephemeral=True)
    if interaction.user.id == info["owner_id"]:
        return await interaction.response.send_message("❌ لا يمكنك إغلاق التذكرة. فقط المسؤول/المستلم.", ephemeral=True)
    if not can_use_admin_commands(interaction, info):
        return await interaction.response.send_message("❌ ليس لديك صلاحية لإغلاق التذكرة.", ephemeral=True)
    await interaction.response.send_message("🗑️ سيتم حذف القناة بعد 3 ثوانٍ...")
    await asyncio.sleep(3)
    try:
        del tickets[str(interaction.channel.id)]
        save_json("tickets", tickets)
    except KeyError:
        pass
    await interaction.channel.delete(reason="إغلاق التذكرة")

@TREE.command(name="convert", description="نقل استلام التذكرة لشخص آخر")
@app_commands.describe(user="الشخص الذي سيتسلم التذكرة بدلًا من الحالي")
async def convert_ticket(interaction: discord.Interaction, user: discord.Member):
    tickets = load_json("tickets")
    info = tickets.get(str(interaction.channel.id))
    if not info:
        return await interaction.response.send_message("❌ هذه ليست قناة تذكرة.", ephemeral=True)
    if interaction.user.id == info["owner_id"]:
        return await interaction.response.send_message("❌ لا يمكنك استعمال هذا الأمر لأنك صاحب التذكرة.", ephemeral=True)
    if not can_use_admin_commands(interaction, info):
        return await interaction.response.send_message("❌ ليس لديك صلاحية لنقل التذكرة.", ephemeral=True)
    config = load_json("config")
    write_role_id = config.get("write_role_id")
    write_role = interaction.guild.get_role(write_role_id) if write_role_id else None
    owner = interaction.guild.get_member(info["owner_id"])
    info["handler_id"] = user.id
    save_json("tickets", tickets)
    await set_ticket_permissions(interaction.channel, owner, user, write_role, locked=True)
    await interaction.response.send_message(f"🔄 تم تحويل التذكرة إلى {user.mention}.")
    try:
        await interaction.channel.send(f"ℹ️ تم تحويل التذكرة إلى {user.mention}.")
    except discord.Forbidden:
        pass

def color_choice_param():
    return [
        app_commands.Choice(name="green", value="green"),
        app_commands.Choice(name="red", value="red"),
        app_commands.Choice(name="blue", value="blue"),
        app_commands.Choice(name="gray", value="gray"),
        app_commands.Choice(name="white", value="white"),
    ]

if __name__ == "__main__":
    ensure_files()
    token = os.getenv("DISCORD_BOT_TOKEN") or "PUT_YOUR_TOKEN_HERE"
    if token == "MTM5NDA5MDg2MzI2ODcyODkxMw.G7NNjd.szCJIVbvLaLacfVmfndFz_iLDssrVWENUJqfbs":
        print("⚠️ ضع توكن البوت في متغير البيئة DISCORD_BOT_TOKEN أو بدّل النص في الملف.")
    BOT.run(token)
