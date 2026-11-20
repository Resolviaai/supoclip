"""
Script to schedule social posts across YouTube, Facebook, Instagram, LinkedIn, and X
with strict compliance to daily rate limits (max 3 clips/day, max 1 post/day for LinkedIn)
and content knowledge auditing.
"""

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List

from src.config import get_config
from src.database import AsyncSessionLocal
from src.services.social_service import SocialService

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("batch_scheduler")

USER_ID = "OpB59NFJKFZqpolm8NM9H4159v422ohr"

ACCOUNTS = {
    "youtube": "84ac3311-5b4d-4666-88e8-bcc3a8237ea8",
    "facebook": "a5a5d6c0-caa9-45e8-9d46-8c0fda6df074",
    "instagram": "b14577b9-ee4a-4cc2-b179-4c3743aa0145",
    "twitter": "03b44502-b691-4ccc-b268-ff3cee4c06ce",
    "linkedin": "8aa5552f-140c-48a4-ae0f-32c53fc31a95",
}

POST_SCHEDULES: List[Dict[str, Any]] = [
    # ----------------------------------------------------
    # Day 1: Friday, Nov 20, 2026 (Slot 1 - 09:32 AM IST)
    # Clip 1: Outreach Math (High B2B Value -> LinkedIn YES)
    # ----------------------------------------------------
    {
        "task_id": "a075e25d-c662-4aa1-8a02-87be6b6405d6",
        "clip_id": "b647bd11-917b-4aa0-bb18-03ddbf59c73f",
        "title": "I Will Delete This Video If 1000 Messages Fail",
        "scheduled_for": "2026-11-20T04:02:00Z",
        "platforms": {
            "youtube": {
                "caption": "Send 20 cold proposals a day. In one month that is 600 people. Repeat it and hit 1,000. It is statistically impossible not to close a client if you stick to the numbers.",
                "hashtags": ["shorts", "freelancing", "clientacquisition", "outreach", "agencygrowth"],
            },
            "facebook": {
                "caption": "Stop relying on luck to get clients. Here is the exact mathematical formula: 20 targeted proposals every day = 600 in 30 days = 1,000+ outreach pipeline. Guaranteed to close deals if you work the system.",
                "hashtags": ["freelance", "outreach", "businessgrowth", "videoediting", "successmindset"],
            },
            "instagram": {
                "caption": "The 1,000 message rule. If you reach out to 20 potential clients every single day, you will contact 600 people a month. By 1,000 messages, closing a high-ticket deal is guaranteed math. Save this reel and start your daily 20 today.",
                "hashtags": ["reels", "freelancing", "outreach", "coldoutreach", "clientacquisition", "agencyhustle"],
            },
            "linkedin": {
                "caption": "Most freelancers fail not because their work is bad, but because their pipeline volume is zero.\n\nHere is the real math of B2B client acquisition:\n- 20 personalized proposals per business day\n- 600 decision-makers reached in 30 days\n- 1,000 total pipeline touchpoints\n\nSales is a law of large numbers. When your volume is high enough, closing deals stops being luck and becomes inevitable.",
                "hashtags": ["Entrepreneurship", "SalesStrategy", "Outreach", "Freelancing", "B2BGrowth"],
            },
            "twitter": {
                "caption": "The 1,000 proposal rule:\n\nReach out to 20 clients/day = 600/month.\n\nHit 1,000 touchpoints. You cannot fail if you work the pipeline numbers. Stop hoping, start reaching out.",
                "hashtags": ["buildinpublic", "freelance", "sales", "growth"],
            },
        },
    },

    # ----------------------------------------------------
    # Day 1: Friday, Nov 20, 2026 (Slot 2 - 01:47 PM IST)
    # Clip 2: 1000 Message Outreach Formula (Iterate & Refine)
    # LinkedIn: NO (Preserving 1/day limit)
    # ----------------------------------------------------
    {
        "task_id": "a075e25d-c662-4aa1-8a02-87be6b6405d6",
        "clip_id": "7ebb5620-3dcd-44bf-8153-5472df5a1a0b",
        "title": "The 1000 Message Outreach Formula For Success",
        "scheduled_for": "2026-11-20T08:17:00Z",
        "platforms": {
            "youtube": {
                "caption": "Sending 10 messages and quitting is why 95% of freelancers remain broke. Outreach + continuous iteration = guaranteed deals.",
                "hashtags": ["shorts", "sales", "freelancetips", "consistency", "mindset"],
            },
            "facebook": {
                "caption": "You can't message 10 people and say 'outreach doesn't work'. Keep reaching out, refining your hook, and improving your portfolio until it clicks.",
                "hashtags": ["salesstrategy", "freelancing", "growth", "discipline"],
            },
            "instagram": {
                "caption": "Don't quit after 10 messages. Outreach is an iterative feedback loop. Send, learn, improve, repeat until you win.",
                "hashtags": ["reels", "coldmessaging", "freelancerlife", "consistencyiskey"],
            },
            "twitter": {
                "caption": "Sending 10 DMs and quitting isn't outreach—it's giving up early.\n\nSend 100, 200, 1,000. Iterate your pitch on every 20. That's how deals get signed.",
                "hashtags": ["growth", "sales", "buildinpublic"],
            },
        },
    },

    # ----------------------------------------------------
    # Day 1: Friday, Nov 20, 2026 (Slot 3 - 06:44 PM IST)
    # Clip 3: Stop Waiting For Clients (Portfolio Myth)
    # LinkedIn: NO (Preserving 1/day limit)
    # ----------------------------------------------------
    {
        "task_id": "a075e25d-c662-4aa1-8a02-87be6b6405d6",
        "clip_id": "d95aa33c-354a-46ab-bd7c-3e5e87654fd1",
        "title": "Stop Waiting For Clients To Message You",
        "scheduled_for": "2026-11-20T13:14:00Z",
        "platforms": {
            "youtube": {
                "caption": "Tweaking your video editing portfolio and waiting for clients to DM you is not a strategy. It's just hoping.",
                "hashtags": ["shorts", "videoediting", "freelanceadvice", "creativebusiness"],
            },
            "facebook": {
                "caption": "Brutal truth for video editors and creatives: perfecting reels all day while waiting for inbound clients will leave you waiting forever. You have to go outbound.",
                "hashtags": ["videoeditor", "freelancing", "clientwork", "creativebusiness"],
            },
            "instagram": {
                "caption": "Stop waiting for clients to magically discover your page. Passive portfolio tweaking is prayer, not business. Take action and pitch directly.",
                "hashtags": ["videoeditors", "premierepro", "freelanceeditor", "creativemindset"],
            },
            "twitter": {
                "caption": "Sprucing up your portfolio for the 100th time hoping a client finds you is not a growth plan. It's prayer.\n\nTake control of your revenue: go outbound.",
                "hashtags": ["videoediting", "creators", "freelance"],
            },
        },
    },

    # ----------------------------------------------------
    # Day 2: Saturday, Nov 21, 2026 (Slot 1 - 10:14 AM IST)
    # Clip 4: Why Passive Waiting Destroys Your Career (High Career Value -> LinkedIn YES)
    # ----------------------------------------------------
    {
        "task_id": "a075e25d-c662-4aa1-8a02-87be6b6405d6",
        "clip_id": "844333bc-baf9-49df-a8b6-5ba0f470d39a",
        "title": "Why Passive Waiting Destroys Your Career",
        "scheduled_for": "2026-11-21T04:44:00Z",
        "platforms": {
            "youtube": {
                "caption": "Nobody is coming to save your career. While you wait for 'luck', competitors are outworking you every single day.",
                "hashtags": ["shorts", "careeradvice", "hustle", "successmindset"],
            },
            "facebook": {
                "caption": "Waiting passively and blaming bad luck is the easiest trap in life. Take full responsibility and do the work.",
                "hashtags": ["discipline", "mindsetshift", "careergoals", "actiontaker"],
            },
            "instagram": {
                "caption": "Passive waiting destroys potential. While you wait, someone with half your skill is working twice as hard and taking your opportunities. Get your hands dirty.",
                "hashtags": ["reels", "careeradvice", "ownership", "disciplineovermotivation"],
            },
            "linkedin": {
                "caption": "The most dangerous mindset in professional life is waiting to be 'lucky'.\n\nWhile you wait:\n- Others are sending proposals\n- Others are refining their skills in public\n- Others are closing your potential clients\n\nHope is not a strategy. Take extreme ownership of your pipeline today.",
                "hashtags": ["Leadership", "CareerGrowth", "ProfessionalDevelopment", "Mindset"],
            },
            "twitter": {
                "caption": "Believing you're 'just unlucky' is a coping mechanism for acting passively.\n\nNobody is coming to hand you opportunities. Out-execute the competition.",
                "hashtags": ["mindset", "discipline", "growth"],
            },
        },
    },

    # ----------------------------------------------------
    # Day 2: Saturday, Nov 21, 2026 (Slot 2 - 06:18 PM IST)
    # Clip 5: Why 100 Messages Lead To Life Changing Deals
    # LinkedIn: NO (Preserving 1/day limit)
    # ----------------------------------------------------
    {
        "task_id": "a075e25d-c662-4aa1-8a02-87be6b6405d6",
        "clip_id": "922d5d3a-faa7-4312-a95a-e4b955a0427e",
        "title": "Why 100 Messages Lead To Life Changing Deals",
        "scheduled_for": "2026-11-21T12:48:00Z",
        "platforms": {
            "youtube": {
                "caption": "100 messages -> 10 replies -> 3 talks -> 1 life-changing deal. If you don't send the 100, your probability of winning is exactly zero.",
                "hashtags": ["shorts", "salesfunnel", "numbersgame", "deals"],
            },
            "facebook": {
                "caption": "The conversion math of life: Send 100 messages, get 10 conversations, close 1 game-changing deal. Simple probability.",
                "hashtags": ["salesmath", "outreach", "freelancebusiness"],
            },
            "instagram": {
                "caption": "The 100-to-1 conversion funnel. 100 pitches -> 10 responses -> 3 negotiations -> 1 life-changing opportunity. Don't leave your chances at zero.",
                "hashtags": ["reels", "salesmetrics", "pipeline", "dealclosing"],
            },
            "twitter": {
                "caption": "The sales funnel in one sentence:\n\n100 outreach -> 10 replies -> 3 talks -> 1 deal.\n\nIf you don't pitch the 100, your chances are literally zero.",
                "hashtags": ["sales", "growth", "pipeline"],
            },
        },
    },

    # ----------------------------------------------------
    # Day 2: Saturday, Nov 21, 2026 (Slot 3 - 08:31 PM IST)
    # Clip 8: Ra.One Was Ahead Of Its Time (Creative & Tech Discussion)
    # LinkedIn: NO (Pop culture/cinema, not professional B2B)
    # ----------------------------------------------------
    {
        "task_id": "359c0d2a-c241-4d7d-8e59-2f0b8b4be8f1",
        "clip_id": "1b8eb2d9-c962-4de0-af68-41bc815ca5b5",
        "title": "Ra.One Was Ahead Of Its Time",
        "scheduled_for": "2026-11-21T15:01:00Z",
        "platforms": {
            "youtube": {
                "caption": "Was Ra.One released 10 years too early? If a Western studio made this concept today, it would be hailed as a sci-fi benchmark.",
                "hashtags": ["shorts", "raone", "indiancinema", "vfx", "animation"],
            },
            "facebook": {
                "caption": "Looking back at Ra.One (2012): The sci-fi gaming concept, VFX ambition, and why the Indian audience wasn't ready back then.",
                "hashtags": ["cinema", "bollywood", "vfx", "scifi"],
            },
            "instagram": {
                "caption": "Ra.One was genuinely ahead of its time. Imagine this concept within the Marvel universe. What do you think?",
                "hashtags": ["reels", "raone", "srk", "vfxindia", "filmanalysis"],
            },
            "twitter": {
                "caption": "Ra.One (2012) was unironically years ahead of its time. High-concept superhero gaming tech released when domestic audience tastes were still catching up.",
                "hashtags": ["cinema", "vfx", "scifi"],
            },
        },
    },

    # ----------------------------------------------------
    # Day 3: Sunday, Nov 22, 2026 (Slot 1 - 11:22 AM IST)
    # Clip 6: The Bahubali Moment For Animation (Industry Benchmark -> LinkedIn YES)
    # ----------------------------------------------------
    {
        "task_id": "359c0d2a-c241-4d7d-8e59-2f0b8b4be8f1",
        "clip_id": "3f35528e-c1f2-4d63-8617-afbb5af3e3a9",
        "title": "The Bahubali Moment For Animation",
        "scheduled_for": "2026-11-22T05:52:00Z",
        "platforms": {
            "youtube": {
                "caption": "Maha Avatar Narasimha is doing for 3D animation what Bahubali did for Indian action cinema. Setting a new industry benchmark.",
                "hashtags": ["shorts", "animation", "bahubali", "indiananimation", "cgi"],
            },
            "facebook": {
                "caption": "Just like Bahubali set a new visual standard for Indian action cinema, Maha Avatar Narasimha is redefining domestic 3D animation standards.",
                "hashtags": ["animationindia", "cinema", "cgi", "creativeindustry"],
            },
            "instagram": {
                "caption": "A landmark moment for Indian animation. The scale, benchmarking, and studio backing behind Maha Avatar Narasimha.",
                "hashtags": ["reels", "3danimation", "indiancreators", "cinematography"],
            },
            "linkedin": {
                "caption": "Every emerging creative industry has its 'Bahubali moment'—the inflection point where production scale and budget efficiency fundamentally reset market expectations.\n\nWith productions like Maha Avatar Narasimha, the Indian 3D animation ecosystem is proving it can create world-class IP with disciplined capital.",
                "hashtags": ["CreativeEconomy", "MediaBusiness", "AnimationIndustry", "IPDevelopment"],
            },
            "twitter": {
                "caption": "Maha Avatar Narasimha is Indian animation's Bahubali moment. A complete reset of production standards and domestic animation ambition.",
                "hashtags": ["animation", "cgi", "creativeindustry"],
            },
        },
    },

    # ----------------------------------------------------
    # Day 3: Sunday, Nov 22, 2026 (Slot 2 - 03:41 PM IST)
    # Clip 9: Why Anime Proves Cultural Impact (Creative / Pop Culture)
    # LinkedIn: NO (Casual consumer discussion)
    # ----------------------------------------------------
    {
        "task_id": "359c0d2a-c241-4d7d-8e59-2f0b8b4be8f1",
        "clip_id": "20a50141-8b12-405b-9b8d-f7e90e813826",
        "title": "Why Anime Proves Cultural Impact",
        "scheduled_for": "2026-11-22T10:11:00Z",
        "platforms": {
            "youtube": {
                "caption": "How Japan used anime to build global soft power—and how Indian animation can do the exact same on the world stage.",
                "hashtags": ["shorts", "anime", "culturalimpact", "globalbranding"],
            },
            "facebook": {
                "caption": "Movies and animation do in one stroke what a thousand speeches cannot. The global power of exported storytelling.",
                "hashtags": ["anime", "storytelling", "culture", "creativeimpact"],
            },
            "instagram": {
                "caption": "The incredible cultural power of animation. From Japan's anime export to India's emerging 3D cinematic mythologies.",
                "hashtags": ["reels", "animefans", "culturalbranding", "animationworld"],
            },
            "twitter": {
                "caption": "Animation is the ultimate cultural soft power. Japan conquered global imagination through anime. India's visual mythologies have the exact same runway.",
                "hashtags": ["anime", "storytelling", "creative"],
            },
        },
    },

    # ----------------------------------------------------
    # Day 3: Sunday, Nov 22, 2026 (Slot 3 - 07:26 PM IST)
    # Clip 10: Maha Avatar Cinematic Universe Until 2037
    # LinkedIn: NO (Entertainment franchise news)
    # ----------------------------------------------------
    {
        "task_id": "359c0d2a-c241-4d7d-8e59-2f0b8b4be8f1",
        "clip_id": "7b482a0d-b407-4c77-926e-6dd651b7396d",
        "title": "Maha Avatar Cinematic Universe Until 2037",
        "scheduled_for": "2026-11-22T13:56:00Z",
        "platforms": {
            "youtube": {
                "caption": "A 10-film animation cinematic universe mapped out all the way to 2037. The ambition here is insane.",
                "hashtags": ["shorts", "cinematicuniverse", "animation", "mythology"],
            },
            "facebook": {
                "caption": "An entire cinematic universe extending up to 2037 with releases planned every two years. Big bets in animation.",
                "hashtags": ["filmuniverse", "animationnews", "cinema"],
            },
            "instagram": {
                "caption": "An Indian animated cinematic universe planned through 2037. Are you excited for this new wave of storytelling?",
                "hashtags": ["reels", "cinematicuniverse", "futureofcinema"],
            },
            "twitter": {
                "caption": "A multi-film animation universe planned out to 2037. Long-term IP planning at its boldest.",
                "hashtags": ["cinema", "animation", "IP"],
            },
        },
    },

    # ----------------------------------------------------
    # Day 4: Monday, Nov 23, 2026 (Slot 1 - 09:48 AM IST)
    # Clip 7: Box Office ROI (Economics & Venture -> LinkedIn YES)
    # ----------------------------------------------------
    {
        "task_id": "359c0d2a-c241-4d7d-8e59-2f0b8b4be8f1",
        "clip_id": "4860b254-6a5b-4b9c-85b6-57f5b72a5bfc",
        "title": "Maha Avatar Narasimha Box Office Success",
        "scheduled_for": "2026-11-23T04:18:00Z",
        "platforms": {
            "youtube": {
                "caption": "Recovering production budget in just 2 days and making 4-5x return with zero live actors. The unit economics of 3D animation.",
                "hashtags": ["shorts", "boxoffice", "animationbusiness", "roi", "cinemabiz"],
            },
            "facebook": {
                "caption": "Breaking down the box office numbers: 4x-5x returns on investment in opening weekend. Why animation is a massive business opportunity.",
                "hashtags": ["business", "boxoffice", "entertainmentbusiness"],
            },
            "instagram": {
                "caption": "Zero live actors, full 3D animation, and 4x-5x budget return in opening days. The incredible business of animation.",
                "hashtags": ["reels", "businessmodel", "boxoffice", "animationeconomics"],
            },
            "linkedin": {
                "caption": "The unit economics of entertainment are shifting.\n\nMaha Avatar Narasimha recovered 100% of its production capital in 48 hours and generated 4–5x return on invested capital—with zero live actors on the payroll.\n\nWhen creative execution meets disciplined CGI pipeline management, scalable IP outperforms traditional production models every single time.",
                "hashtags": ["EntertainmentFinance", "VentureCapital", "MediaEconomics", "BusinessStrategy"],
            },
            "twitter": {
                "caption": "100% budget recouped in 48 hours, 4-5x ROI, zero live actors.\n\n3D animation is the most capital-efficient IP engine in entertainment today.",
                "hashtags": ["business", "entertainment", "ROI"],
            },
        },
    },
]


async def main():
    config = get_config()
    total_scheduled = 0
    errors = 0

    async with AsyncSessionLocal() as db:
        service = SocialService(db, config)

        for clip_item in POST_SCHEDULES:
            task_id = clip_item["task_id"]
            clip_id = clip_item["clip_id"]
            title = clip_item["title"]
            scheduled_for = clip_item["scheduled_for"]
            platforms_dict = clip_item["platforms"]

            logger.info("--- Scheduling clip: %s (%s) ---", title, clip_id)

            for platform_name, post_info in platforms_dict.items():
                account_id = ACCOUNTS.get(platform_name)
                if not account_id:
                    logger.warning("No account found for platform %s", platform_name)
                    continue

                caption = post_info["caption"]
                hashtags = post_info["hashtags"]

                try:
                    post = await service.create_post(
                        USER_ID,
                        task_id=task_id,
                        clip_id=clip_id,
                        social_account_id=account_id,
                        title=title,
                        caption=caption,
                        hashtags=hashtags,
                        privacy_level="public",
                        scheduled_for=scheduled_for,
                    )
                    logger.info(
                        "Created post %s on %s at %s (status: %s)",
                        post["id"],
                        platform_name,
                        scheduled_for,
                        post["status"],
                    )
                    total_scheduled += 1
                except Exception as exc:
                    logger.error(
                        "Failed to schedule on %s for clip %s: %s",
                        platform_name,
                        clip_id,
                        exc,
                    )
                    errors += 1

    logger.info("=== SUMMARY ===")
    logger.info("Successfully scheduled: %d posts", total_scheduled)
    logger.info("Errors: %d", errors)


if __name__ == "__main__":
    asyncio.run(main())
