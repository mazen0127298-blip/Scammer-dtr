import os
import subprocess
import json
import threading

# ✅ تثبيت المكتبات المطلوبة تلقائيًا
required_libs = ["discord.py", "flask"]
for lib in required_libs:
    try:
        __import__(lib.split('.')[0])
    except ImportError:
        subprocess.check_call(["pip", "install", lib])

import discord
from discord.ext import commands
from discord import app_commands
from flask import Flask

# 🔐 التوكن من متغير البيئة
TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise ValueError("❌ لم يتم العثور على التوكن. تأكد أنك ضايفه في إعدادات Render تحت Environment Variables باسم DISCORD_TOKEN.")

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# 📁 ملف بيانات مشترك لكل السيرفرات
if not os.path.exists("data"):
    os.makedirs("data")

GLOBAL_DATA_PATH = "data/global_reports.json"

def get_global_data():
    if not os.path.exists(GLOBAL_DATA_PATH):
        with open(GLOBAL_DATA_PATH, "w") as f:
            json.dump({"reports": [], "permissions": {}}, f)
    with open(GLOBAL_DATA_PATH, "r") as f:
        return json.load(f)

def save_global_data(data):
    with open(GLOBAL_DATA_PATH, "w") as f:
        json.dump(data, f, indent=2)

def has_permission(interaction, command_name):
    guild = interaction.guild
    if not guild:
        return False
    member = interaction.user

    if member.guild_permissions.administrator:
        return True

    top_roles = sorted(guild.roles, key=lambda r: r.position, reverse=True)[:10]
    if any(role in member.roles for role in top_roles):
        return True

    data = get_global_data()
    allowed_roles = data.get("permissions", {}).get(command_name, [])
    if any(role.id in allowed_roles for role in member.roles):
        return True

    return False

@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"✅ البوت اشتغل - اسم البوت: {bot.user}")

# ===== أوامر الإدارة =====
@bot.tree.command(name="role", description="إدارة صلاحيات الأوامر لرتب معينة")
@app_commands.describe(
    action="اختيار العملية: إضافة، حذف، عرض",
    role="الرتبة المستهدفة",
    command_name="اسم الأمر: scammer, oppressed, restart, role"
)
async def role_command(interaction: discord.Interaction, action: str, role: discord.Role, command_name: str):
    if not has_permission(interaction, "role"):
        await interaction.response.send_message("❌ ليس لديك صلاحية لهذا الأمر.", ephemeral=True)
        return

    data = get_global_data()
    if "permissions" not in data:
        data["permissions"] = {}

    if command_name not in data["permissions"]:
        data["permissions"][command_name] = []

    role_id = role.id

    if action == "add":
        if role_id not in data["permissions"][command_name]:
            data["permissions"][command_name].append(role_id)
            save_global_data(data)
            await interaction.response.send_message(f"✅ تمت إضافة صلاحية لرتبة {role.mention} على أمر {command_name}", ephemeral=True)
        else:
            await interaction.response.send_message("⚠️ هذه الرتبة لديها الصلاحية بالفعل.", ephemeral=True)

    elif action == "remove":
        if role_id in data["permissions"][command_name]:
            data["permissions"][command_name].remove(role_id)
            save_global_data(data)
            await interaction.response.send_message(f"🗑️ تمت إزالة صلاحية {role.mention} من أمر {command_name}", ephemeral=True)
        else:
            await interaction.response.send_message("⚠️ هذه الرتبة لا تمتلك صلاحية لهذا الأمر.", ephemeral=True)

    elif action == "show":
        roles = [interaction.guild.get_role(rid) for rid in data["permissions"].get(command_name, [])]
        roles_mentions = [r.mention for r in roles if r]
        msg = "\n".join(roles_mentions) if roles_mentions else "لا توجد رتب لديها صلاحية لهذا الأمر."
        await interaction.response.send_message(f"📋 الرتب التي تملك صلاحية `{command_name}`:\n{msg}", ephemeral=True)

    else:
        await interaction.response.send_message("❌ العملية غير صحيحة. استخدم: add أو remove أو show", ephemeral=True)

# ===== أوامر البلاغات =====
@bot.tree.command(name="scammer", description="الإبلاغ عن نصّاب مع دليل (حتى لو مش موجود في السيرفر)")
@app_commands.describe(
    user="ID أو Username للشخص النصّاب",
    story="تفاصيل القصة",
    proof="الدليل (صورة أو فيديو)"
)
async def scammer(interaction: discord.Interaction, user: str, story: str, proof: discord.Attachment):
    if not has_permission(interaction, "scammer"):
        await interaction.response.send_message("❌ لا تملك صلاحية استخدام هذا الأمر.", ephemeral=True)
        return

    data = get_global_data()

    # محاولة التعرف على المستخدم
    scammer_id = None
    scammer_name = user
    if user.isdigit():
        scammer_id = int(user)
        member_obj = interaction.guild.get_member(scammer_id)
        if member_obj:
            scammer_name = member_obj.name

    report = {
        "reported_by": interaction.user.id,
        "scammer_id": scammer_id,
        "scammer_name": scammer_name,
        "story": story,
        "proof": proof.url
    }
    data["reports"].append(report)
    save_global_data(data)

    await interaction.response.send_message(f"✅ تم الإبلاغ عن {scammer_name}", ephemeral=True)

@bot.tree.command(name="oppressed", description="حذف بلاغ معين ضد شخص")
@app_commands.describe(
    user="ID أو اسم الشخص",
    report_number="رقم البلاغ المراد حذفه"
)
async def oppressed(interaction: discord.Interaction, user: str, report_number: int):
    if not has_permission(interaction, "oppressed"):
        await interaction.response.send_message("❌ لا تملك صلاحية استخدام هذا الأمر.", ephemeral=True)
        return

    data = get_global_data()

    if user.isdigit():
        scammer_id = int(user)
        reports = [r for r in data["reports"] if r["scammer_id"] == scammer_id]
    else:
        reports = [r for r in data["reports"] if r["scammer_name"].lower() == user.lower()]

    if report_number < 1 or report_number > len(reports):
        await interaction.response.send_message("⚠️ رقم البلاغ غير صالح.", ephemeral=True)
        return

    target_report = reports[report_number - 1]
    data["reports"].remove(target_report)
    save_global_data(data)

    await interaction.response.send_message(f"🗑️ تم حذف البلاغ رقم {report_number} ضد {user}", ephemeral=True)

@bot.tree.command(name="restart", description="حذف جميع البلاغات ضد شخص معين")
@app_commands.describe(
    user="ID أو اسم الشخص"
)
async def restart(interaction: discord.Interaction, user: str):
    if not has_permission(interaction, "restart"):
        await interaction.response.send_message("❌ لا تملك صلاحية استخدام هذا الأمر.", ephemeral=True)
        return

    data = get_global_data()

    before_count = len(data["reports"])
    if user.isdigit():
        scammer_id = int(user)
        data["reports"] = [r for r in data["reports"] if r["scammer_id"] != scammer_id]
    else:
        data["reports"] = [r for r in data["reports"] if r["scammer_name"].lower() != user.lower()]
    after_count = len(data["reports"])
    save_global_data(data)

    deleted = before_count - after_count
    await interaction.response.send_message(f"🔁 تم حذف {deleted} من البلاغات ضد {user}", ephemeral=True)

# ===== أمر عرض البلاغات =====
@bot.command(name="dtr")
async def dtr(ctx, user: str):
    data = get_global_data()

    if user.isdigit():
        user_id = int(user)
        reports = [r for r in data["reports"] if r["scammer_id"] == user_id]
    else:
        reports = [r for r in data["reports"] if r["scammer_name"].lower() == user.lower()]

    if not reports:
        await ctx.send(f"❌ لا توجد بلاغات ضد {user}")
        return

    for i, rep in enumerate(reports, 1):
        embed = discord.Embed(
            title=f"📂 بلاغ رقم {i} ضد {rep['scammer_name']}",
            description=f"👤 المُبلّغ: <@{rep['reported_by']}>\n📝 القصة: {rep['story']}",
            color=discord.Color.red()
        )
        embed.set_image(url=rep["proof"])
        await ctx.send(embed=embed)

# 🌐 سيرفر ويب صغير لـ UptimeRobot
app = Flask(__name__)

@app.route("/")
def home():
    return "Bot is Alive ✅"

def run_web():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))

threading.Thread(target=run_web).start()

# 🚀 تشغيل البوت
bot.run(TOKEN)
