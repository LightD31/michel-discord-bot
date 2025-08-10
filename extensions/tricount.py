import os
from datetime import datetime
from typing import Optional, Union

import pymongo
from interactions import (
    AutocompleteContext,
    Client,
    Embed,
    Extension,
    OptionType,
    SlashContext,
    User,
    Member,
    slash_command,
    slash_option,
)

from src import logutil
from src.utils import load_config

logger = logutil.init_logger(os.path.basename(__file__))
config, module_config, enabled_servers = load_config("moduleTricount")


class TricountClass(Extension):
    def __init__(self, bot):
        self.bot: Client = bot
        # Database connection
        client = pymongo.MongoClient(config["mongodb"]["url"])
        db = client["Playlist"]
        self.groups_collection = db["tricount_groups"]
        self.expenses_collection = db["tricount_expenses"]

    @slash_command(
        name="tricount",
        description="Gestion des dépenses partagées",
        sub_cmd_name="groupe",
        sub_cmd_description="Créer un nouveau groupe de dépenses",
    )
    @slash_option(
        name="nom",
        description="Nom du groupe",
        opt_type=OptionType.STRING,
        required=True,
        max_length=50,
    )
    @slash_option(
        name="description",
        description="Description du groupe",
        opt_type=OptionType.STRING,
        required=False,
        max_length=200,
    )
    async def tricount_groupe(
        self,
        ctx: SlashContext,
        nom: str,
        description: Optional[str] = None,
    ):
        # Vérification de sécurité
        if not ctx.guild:
            await ctx.send("❌ Cette commande ne peut être utilisée que dans un serveur.", ephemeral=True)
            return
            
        # Vérifier si un groupe avec ce nom existe déjà sur ce serveur
        existing_group = self.groups_collection.find_one({
            "name": nom,
            "server": ctx.guild.id
        })
        
        if existing_group:
            await ctx.send(f"❌ Un groupe avec le nom '{nom}' existe déjà sur ce serveur.", ephemeral=True)
            return

        # Créer le nouveau groupe
        group_data = {
            "name": nom,
            "description": description or "",
            "server": ctx.guild.id,
            "creator": ctx.author.id,
            "members": [ctx.author.id],
            "created_at": datetime.now(),
            "is_active": True
        }
        
        result = self.groups_collection.insert_one(group_data)
        group_id = result.inserted_id
        
        embed = Embed(
            title="✅ Groupe créé",
            description=f"Le groupe **{nom}** a été créé avec succès !",
            color=0x00FF00,
        )
        embed.add_field(name="ID du groupe", value=str(group_id), inline=True)
        embed.add_field(name="Créateur", value=ctx.author.mention, inline=True)
        if description:
            embed.add_field(name="Description", value=description, inline=False)
            
        logger.info(
            "Groupe Tricount '%s' créé par %s sur le serveur %s",
            nom,
            ctx.author.display_name,
            ctx.guild.name,
        )
        
        await ctx.send(embed=embed)

    @tricount_groupe.subcommand(
        sub_cmd_name="rejoindre",
        sub_cmd_description="Rejoindre un groupe existant",
    )
    @slash_option(
        name="nom",
        description="Nom du groupe à rejoindre",
        opt_type=OptionType.STRING,
        required=True,
        autocomplete=True,
    )
    async def tricount_rejoindre(self, ctx: SlashContext, nom: str):
        if not ctx.guild:
            await ctx.send("❌ Cette commande ne peut être utilisée que dans un serveur.", ephemeral=True)
            return
            
        # Trouver le groupe
        group = self.groups_collection.find_one({
            "name": nom,
            "server": ctx.guild.id,
            "is_active": True
        })
        
        if not group:
            await ctx.send(f"❌ Aucun groupe actif trouvé avec le nom '{nom}'.", ephemeral=True)
            return
            
        # Vérifier si l'utilisateur est déjà membre
        if ctx.author.id in group["members"]:
            await ctx.send("❌ Vous êtes déjà membre de ce groupe.", ephemeral=True)
            return
            
        # Ajouter l'utilisateur au groupe
        self.groups_collection.update_one(
            {"_id": group["_id"]},
            {"$push": {"members": ctx.author.id}}
        )
        
        embed = Embed(
            title="✅ Groupe rejoint",
            description=f"Vous avez rejoint le groupe **{nom}** !",
            color=0x00FF00,
        )
        
        logger.info(
            "Utilisateur %s a rejoint le groupe Tricount '%s' sur le serveur %s",
            ctx.author.display_name,
            nom,
            ctx.guild.name,
        )
        
        await ctx.send(embed=embed)

    @tricount_rejoindre.autocomplete("nom")
    async def groupe_autocomplete(self, ctx: AutocompleteContext):
        if not ctx.guild:
            await ctx.send(choices=[])
            return
            
        input_text = ctx.input_text.lower()
        groups = self.groups_collection.find({
            "server": ctx.guild.id,
            "is_active": True
        })
        
        filtered_groups = []
        for group in groups:
            if input_text in group["name"].lower():
                filtered_groups.append({
                    "name": group["name"],
                    "value": group["name"]
                })
                
        # Limiter à 25 résultats
        filtered_groups = filtered_groups[:25]
        await ctx.send(choices=filtered_groups)

    @tricount_groupe.subcommand(
        sub_cmd_name="quitter",
        sub_cmd_description="Quitter un groupe",
    )
    @slash_option(
        name="nom",
        description="Nom du groupe à quitter",
        opt_type=OptionType.STRING,
        required=True,
        autocomplete=True,
    )
    async def tricount_quitter(self, ctx: SlashContext, nom: str):
        if not ctx.guild:
            await ctx.send("❌ Cette commande ne peut être utilisée que dans un serveur.", ephemeral=True)
            return
            
        # Trouver le groupe
        group = self.groups_collection.find_one({
            "name": nom,
            "server": ctx.guild.id,
            "is_active": True
        })
        
        if not group:
            await ctx.send(f"❌ Aucun groupe actif trouvé avec le nom '{nom}'.", ephemeral=True)
            return
            
        # Vérifier si l'utilisateur est membre
        if ctx.author.id not in group["members"]:
            await ctx.send("❌ Vous n'êtes pas membre de ce groupe.", ephemeral=True)
            return
            
        # Retirer l'utilisateur du groupe
        self.groups_collection.update_one(
            {"_id": group["_id"]},
            {"$pull": {"members": ctx.author.id}}
        )
        
        embed = Embed(
            title="✅ Groupe quitté",
            description=f"Vous avez quitté le groupe **{nom}**.",
            color=0xFF9900,
        )
        
        logger.info(
            "Utilisateur %s a quitté le groupe Tricount '%s' sur le serveur %s",
            ctx.author.display_name,
            nom,
            ctx.guild.name,
        )
        
        await ctx.send(embed=embed)

    @tricount_quitter.autocomplete("nom")
    async def groupe_membre_autocomplete(self, ctx: AutocompleteContext):
        if not ctx.guild:
            await ctx.send(choices=[])
            return
            
        input_text = ctx.input_text.lower()
        groups = self.groups_collection.find({
            "server": ctx.guild.id,
            "is_active": True,
            "members": ctx.author.id
        })
        
        filtered_groups = []
        for group in groups:
            if input_text in group["name"].lower():
                filtered_groups.append({
                    "name": group["name"],
                    "value": group["name"]
                })
                
        filtered_groups = filtered_groups[:25]
        await ctx.send(choices=filtered_groups)

    @slash_command(
        name="depense",
        description="Ajouter une dépense",
    )
    @slash_option(
        name="groupe",
        description="Nom du groupe",
        opt_type=OptionType.STRING,
        required=True,
        autocomplete=True,
    )
    @slash_option(
        name="montant",
        description="Montant de la dépense (en euros)",
        opt_type=OptionType.NUMBER,
        required=True,
    )
    @slash_option(
        name="description",
        description="Description de la dépense",
        opt_type=OptionType.STRING,
        required=True,
        max_length=100,
    )
    @slash_option(
        name="payeur",
        description="Qui a payé (par défaut: vous)",
        opt_type=OptionType.USER,
        required=False,
    )
    async def depense(
        self,
        ctx: SlashContext,
        groupe: str,
        montant: float,
        description: str,
        payeur: Optional[Union[User, Member]] = None,
    ):
        if not ctx.guild:
            await ctx.send("❌ Cette commande ne peut être utilisée que dans un serveur.", ephemeral=True)
            return
            
        if payeur is None:
            payeur = ctx.author
            
        # Vérifier que le montant est positif
        if montant <= 0:
            await ctx.send("❌ Le montant doit être positif.", ephemeral=True)
            return
            
        # Trouver le groupe
        group = self.groups_collection.find_one({
            "name": groupe,
            "server": ctx.guild.id,
            "is_active": True
        })
        
        if not group:
            await ctx.send(f"❌ Aucun groupe actif trouvé avec le nom '{groupe}'.", ephemeral=True)
            return
            
        # Vérifier que l'utilisateur est membre du groupe
        if ctx.author.id not in group["members"]:
            await ctx.send("❌ Vous devez être membre du groupe pour ajouter une dépense.", ephemeral=True)
            return
            
        # Vérifier que le payeur est membre du groupe
        if payeur.id not in group["members"]:
            await ctx.send("❌ Le payeur doit être membre du groupe.", ephemeral=True)
            return
            
        # Créer la dépense
        expense_data = {
            "group_id": group["_id"],
            "group_name": groupe,
            "server": ctx.guild.id,
            "amount": round(montant, 2),
            "description": description,
            "payer": payeur.id,
            "added_by": ctx.author.id,
            "participants": group["members"],  # Par défaut, tous les membres participent
            "date": datetime.now(),
        }
        
        self.expenses_collection.insert_one(expense_data)
        
        embed = Embed(
            title="✅ Dépense ajoutée",
            description=f"Dépense ajoutée au groupe **{groupe}**",
            color=0x00FF00,
        )
        embed.add_field(name="Montant", value=f"{montant:.2f}€", inline=True)
        embed.add_field(name="Payeur", value=payeur.mention, inline=True)
        embed.add_field(name="Description", value=description, inline=False)
        embed.add_field(
            name="Part par personne", 
            value=f"{montant / len(group['members']):.2f}€", 
            inline=True
        )
        
        logger.info(
            "Dépense de %.2f€ ajoutée par %s au groupe '%s' (payeur: %s)",
            montant,
            ctx.author.display_name,
            groupe,
            payeur.display_name,
        )
        
        await ctx.send(embed=embed)

    @depense.autocomplete("groupe")
    async def depense_groupe_autocomplete(self, ctx: AutocompleteContext):
        if not ctx.guild:
            await ctx.send(choices=[])
            return
            
        input_text = ctx.input_text.lower()
        groups = self.groups_collection.find({
            "server": ctx.guild.id,
            "is_active": True,
            "members": ctx.author.id
        })
        
        filtered_groups = []
        for group in groups:
            if input_text in group["name"].lower():
                filtered_groups.append({
                    "name": group["name"],
                    "value": group["name"]
                })
                
        filtered_groups = filtered_groups[:25]
        await ctx.send(choices=filtered_groups)

    @slash_command(
        name="bilan",
        description="Voir le bilan des dépenses d'un groupe",
    )
    @slash_option(
        name="groupe",
        description="Nom du groupe",
        opt_type=OptionType.STRING,
        required=True,
        autocomplete=True,
    )
    async def bilan(self, ctx: SlashContext, groupe: str):
        if not ctx.guild:
            await ctx.send("❌ Cette commande ne peut être utilisée que dans un serveur.", ephemeral=True)
            return
            
        # Trouver le groupe
        group = self.groups_collection.find_one({
            "name": groupe,
            "server": ctx.guild.id,
            "is_active": True
        })
        
        if not group:
            await ctx.send(f"❌ Aucun groupe actif trouvé avec le nom '{groupe}'.", ephemeral=True)
            return
            
        # Vérifier que l'utilisateur est membre du groupe
        if ctx.author.id not in group["members"]:
            await ctx.send("❌ Vous devez être membre du groupe pour voir le bilan.", ephemeral=True)
            return
            
        # Récupérer toutes les dépenses du groupe
        expenses = list(self.expenses_collection.find({"group_id": group["_id"]}))
        
        if not expenses:
            embed = Embed(
                title=f"📊 Bilan du groupe {groupe}",
                description="Aucune dépense enregistrée.",
                color=0x0099FF,
            )
            await ctx.send(embed=embed)
            return
            
        # Calculer les totaux
        total_expenses = sum(expense["amount"] for expense in expenses)
        num_members = len(group["members"])
        cost_per_person = total_expenses / num_members
        
        # Calculer ce que chaque personne a payé et doit
        balances = {}
        for member_id in group["members"]:
            paid = sum(expense["amount"] for expense in expenses if expense["payer"] == member_id)
            owes = cost_per_person
            balance = paid - owes
            balances[member_id] = {
                "paid": paid,
                "owes": owes,
                "balance": balance
            }
        
        # Créer l'embed principal
        embed = Embed(
            title=f"📊 Bilan du groupe {groupe}",
            description=f"**Total des dépenses:** {total_expenses:.2f}€\n**Coût par personne:** {cost_per_person:.2f}€",
            color=0x0099FF,
        )
        
        # Ajouter les balances
        balance_text = ""
        for member_id, data in balances.items():
            try:
                user = await self.bot.fetch_user(member_id)
                if user:  # Vérifier que l'utilisateur existe
                    if data["balance"] > 0.01:  # Petit seuil pour éviter les erreurs d'arrondi
                        balance_text += f"💰 {user.mention}: +{data['balance']:.2f}€ (a payé {data['paid']:.2f}€)\n"
                    elif data["balance"] < -0.01:
                        balance_text += f"💸 {user.mention}: {data['balance']:.2f}€ (a payé {data['paid']:.2f}€)\n"
                    else:
                        balance_text += f"✅ {user.mention}: 0€ (a payé {data['paid']:.2f}€)\n"
                else:
                    balance_text += f"❓ Utilisateur inconnu: {data['balance']:.2f}€\n"
            except Exception:
                balance_text += f"❓ Utilisateur inconnu: {data['balance']:.2f}€\n"
                
        embed.add_field(name="Balances", value=balance_text or "Aucune donnée", inline=False)
        
        await ctx.send(embed=embed)

    @bilan.autocomplete("groupe")
    async def bilan_groupe_autocomplete(self, ctx: AutocompleteContext):
        if not ctx.guild:
            await ctx.send(choices=[])
            return
            
        input_text = ctx.input_text.lower()
        groups = self.groups_collection.find({
            "server": ctx.guild.id,
            "is_active": True,
            "members": ctx.author.id
        })
        
        filtered_groups = []
        for group in groups:
            if input_text in group["name"].lower():
                filtered_groups.append({
                    "name": group["name"],
                    "value": group["name"]
                })
                
        filtered_groups = filtered_groups[:25]
        await ctx.send(choices=filtered_groups)

    @slash_command(
        name="mes-groupes",
        description="Voir tous vos groupes Tricount",
    )
    async def mes_groupes(self, ctx: SlashContext):
        if not ctx.guild:
            await ctx.send("❌ Cette commande ne peut être utilisée que dans un serveur.", ephemeral=True)
            return
            
        # Récupérer tous les groupes dont l'utilisateur est membre
        groups = list(self.groups_collection.find({
            "server": ctx.guild.id,
            "is_active": True,
            "members": ctx.author.id
        }))
        
        if not groups:
            embed = Embed(
                title="📝 Mes groupes Tricount",
                description="Vous n'êtes membre d'aucun groupe.",
                color=0x0099FF,
            )
            await ctx.send(embed=embed)
            return
            
        embed = Embed(
            title="📝 Mes groupes Tricount",
            description=f"Vous êtes membre de {len(groups)} groupe(s):",
            color=0x0099FF,
        )
        
        for group in groups[:10]:  # Limiter à 10 groupes pour éviter les embeds trop longs
            # Calculer le nombre de dépenses
            expense_count = self.expenses_collection.count_documents({"group_id": group["_id"]})
            total_amount = sum(
                expense["amount"] 
                for expense in self.expenses_collection.find({"group_id": group["_id"]})
            )
            
            field_value = f"**Membres:** {len(group['members'])}\n"
            field_value += f"**Dépenses:** {expense_count}\n"
            field_value += f"**Total:** {total_amount:.2f}€\n"
            if group["description"]:
                field_value += f"**Description:** {group['description']}"
                
            embed.add_field(
                name=group["name"],
                value=field_value,
                inline=True
            )
            
        await ctx.send(embed=embed)
