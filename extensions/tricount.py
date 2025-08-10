import os
from datetime import datetime
from typing import Optional, Union

import pymongo
from bson import ObjectId
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
        name="modifier-depense",
        description="Modifier une dépense existante",
    )
    @slash_option(
        name="groupe",
        description="Nom du groupe",
        opt_type=OptionType.STRING,
        required=True,
        autocomplete=True,
    )
    @slash_option(
        name="depense_id",
        description="ID de la dépense à modifier",
        opt_type=OptionType.STRING,
        required=True,
        autocomplete=True,
    )
    @slash_option(
        name="nouveau_montant",
        description="Nouveau montant (laissez vide pour ne pas modifier)",
        opt_type=OptionType.NUMBER,
        required=False,
    )
    @slash_option(
        name="nouvelle_description",
        description="Nouvelle description (laissez vide pour ne pas modifier)",
        opt_type=OptionType.STRING,
        required=False,
        max_length=100,
    )
    @slash_option(
        name="nouveau_payeur",
        description="Nouveau payeur (laissez vide pour ne pas modifier)",
        opt_type=OptionType.USER,
        required=False,
    )
    async def modifier_depense(
        self,
        ctx: SlashContext,
        groupe: str,
        depense_id: str,
        nouveau_montant: Optional[float] = None,
        nouvelle_description: Optional[str] = None,
        nouveau_payeur: Optional[Union[User, Member]] = None,
    ):
        if not ctx.guild:
            await ctx.send("❌ Cette commande ne peut être utilisée que dans un serveur.", ephemeral=True)
            return
            
        # Vérifier que le montant est positif si fourni
        if nouveau_montant is not None and nouveau_montant <= 0:
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
            await ctx.send("❌ Vous devez être membre du groupe pour modifier une dépense.", ephemeral=True)
            return
            
        # Trouver la dépense
        try:
            expense = self.expenses_collection.find_one({
                "_id": ObjectId(depense_id),
                "group_id": group["_id"]
            })
        except Exception:
            await ctx.send("❌ ID de dépense invalide.", ephemeral=True)
            return
            
        if not expense:
            await ctx.send("❌ Dépense non trouvée dans ce groupe.", ephemeral=True)
            return
            
        # Vérifier que l'utilisateur peut modifier cette dépense (créateur ou payeur)
        if ctx.author.id != expense["added_by"] and ctx.author.id != expense["payer"]:
            await ctx.send("❌ Vous ne pouvez modifier que les dépenses que vous avez ajoutées ou payées.", ephemeral=True)
            return
            
        # Vérifier que le nouveau payeur est membre du groupe si fourni
        if nouveau_payeur and nouveau_payeur.id not in group["members"]:
            await ctx.send("❌ Le nouveau payeur doit être membre du groupe.", ephemeral=True)
            return
            
        # Préparer les modifications
        modifications = {}
        changes_description = []
        
        if nouveau_montant is not None:
            modifications["amount"] = round(nouveau_montant, 2)
            changes_description.append(f"Montant: {expense['amount']:.2f}€ → {nouveau_montant:.2f}€")
            
        if nouvelle_description is not None:
            modifications["description"] = nouvelle_description
            changes_description.append(f"Description: '{expense['description']}' → '{nouvelle_description}'")
            
        if nouveau_payeur is not None:
            modifications["payer"] = nouveau_payeur.id
            old_payer = await self.bot.fetch_user(expense["payer"])
            old_payer_name = old_payer.display_name if old_payer else "Utilisateur inconnu"
            changes_description.append(f"Payeur: {old_payer_name} → {nouveau_payeur.display_name}")
            
        if not modifications:
            await ctx.send("❌ Aucune modification spécifiée.", ephemeral=True)
            return
            
        # Mettre à jour la dépense
        self.expenses_collection.update_one(
            {"_id": ObjectId(depense_id)},
            {"$set": modifications}
        )
        
        embed = Embed(
            title="✅ Dépense modifiée",
            description=f"La dépense dans le groupe **{groupe}** a été modifiée.",
            color=0x00FF00,
        )
        embed.add_field(
            name="Modifications",
            value="\n".join(changes_description),
            inline=False
        )
        
        logger.info(
            "Dépense %s modifiée par %s dans le groupe '%s': %s",
            depense_id,
            ctx.author.display_name,
            groupe,
            ", ".join(changes_description),
        )
        
        await ctx.send(embed=embed)

    @modifier_depense.autocomplete("groupe")
    async def modifier_depense_groupe_autocomplete(self, ctx: AutocompleteContext):
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

    @modifier_depense.autocomplete("depense_id")
    async def modifier_depense_id_autocomplete(self, ctx: AutocompleteContext):
        if not ctx.guild:
            await ctx.send(choices=[])
            return
            
        # Pour simplifier, on récupère toutes les dépenses de l'utilisateur sur ce serveur
        # L'utilisateur devra d'abord choisir le groupe
        input_text = ctx.input_text.lower()
        
        # Récupérer tous les groupes de l'utilisateur
        user_groups = list(self.groups_collection.find({
            "server": ctx.guild.id,
            "is_active": True,
            "members": ctx.author.id
        }))
        
        if not user_groups:
            await ctx.send(choices=[])
            return
            
        group_ids = [group["_id"] for group in user_groups]
        
        # Récupérer les dépenses que l'utilisateur peut modifier
        expenses = self.expenses_collection.find({
            "group_id": {"$in": group_ids},
            "$or": [
                {"added_by": ctx.author.id},
                {"payer": ctx.author.id}
            ]
        }).sort("date", -1).limit(25)
        
        choices = []
        for expense in expenses:
            # Trouver le nom du groupe
            group_name = "Groupe inconnu"
            for group in user_groups:
                if group["_id"] == expense["group_id"]:
                    group_name = group["name"]
                    break
            
            # Créer un nom descriptif pour la dépense
            payer_name = f"ID:{expense['payer']}"
            try:
                payer = self.bot.get_user(expense["payer"])
                if payer:
                    payer_name = payer.display_name
            except Exception:
                pass
                
            display_name = f"[{group_name}] {expense['amount']:.2f}€ - {expense['description']} (par {payer_name})"
            if len(display_name) > 80:
                display_name = display_name[:77] + "..."
                
            # Filtrer par texte d'entrée
            if input_text in display_name.lower():
                choices.append({
                    "name": display_name,
                    "value": str(expense["_id"])
                })
            
        await ctx.send(choices=choices[:25])

    @slash_command(
        name="liste-depenses",
        description="Voir la liste des dépenses d'un groupe",
    )
    @slash_option(
        name="groupe",
        description="Nom du groupe",
        opt_type=OptionType.STRING,
        required=True,
        autocomplete=True,
    )
    @slash_option(
        name="limite",
        description="Nombre de dépenses à afficher (défaut: 10, max: 20)",
        opt_type=OptionType.INTEGER,
        required=False,
        min_value=1,
        max_value=20,
    )
    async def liste_depenses(self, ctx: SlashContext, groupe: str, limite: Optional[int] = 10):
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
            await ctx.send("❌ Vous devez être membre du groupe pour voir les dépenses.", ephemeral=True)
            return
            
        # Récupérer les dépenses du groupe (les plus récentes d'abord)
        limit_value = limite if limite is not None else 10
        expenses = list(self.expenses_collection.find({"group_id": group["_id"]}).sort("date", -1).limit(limit_value))
        
        if not expenses:
            embed = Embed(
                title=f"📋 Dépenses du groupe {groupe}",
                description="Aucune dépense enregistrée.",
                color=0x0099FF,
            )
            await ctx.send(embed=embed)
            return
            
        embed = Embed(
            title=f"📋 Dépenses du groupe {groupe}",
            description=f"Dernières {len(expenses)} dépense(s):",
            color=0x0099FF,
        )
        
        for i, expense in enumerate(expenses, 1):
            # Récupérer les informations du payeur
            payer_name = "Utilisateur inconnu"
            try:
                payer = self.bot.get_user(expense["payer"])
                if payer:
                    payer_name = payer.display_name
                else:
                    # Essayer de fetch l'utilisateur
                    payer = await self.bot.fetch_user(expense["payer"])
                    if payer:
                        payer_name = payer.display_name
            except Exception:
                payer_name = f"ID:{expense['payer']}"
            
            # Récupérer les informations de qui a ajouté la dépense
            added_by_name = "Utilisateur inconnu"
            try:
                added_by = self.bot.get_user(expense["added_by"])
                if added_by:
                    added_by_name = added_by.display_name
                else:
                    added_by = await self.bot.fetch_user(expense["added_by"])
                    if added_by:
                        added_by_name = added_by.display_name
            except Exception:
                added_by_name = f"ID:{expense['added_by']}"
            
            # Formater la date
            date_str = expense["date"].strftime("%d/%m/%Y %H:%M")
            
            field_value = f"**Montant:** {expense['amount']:.2f}€\n"
            field_value += f"**Payeur:** {payer_name}\n"
            field_value += f"**Ajouté par:** {added_by_name}\n"
            field_value += f"**Date:** {date_str}\n"
            field_value += f"**ID:** `{expense['_id']}`"
            
            embed.add_field(
                name=f"{i}. {expense['description']}",
                value=field_value,
                inline=True
            )
            
        embed.set_footer(text="💡 Utilisez l'ID pour modifier une dépense avec /modifier-depense")
        await ctx.send(embed=embed)

    @liste_depenses.autocomplete("groupe")
    async def liste_depenses_groupe_autocomplete(self, ctx: AutocompleteContext):
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
