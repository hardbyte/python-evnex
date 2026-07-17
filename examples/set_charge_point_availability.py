import asyncio
import logging
import os

from evnex.api import Evnex
from evnex.auth import EvnexAuth

logging.basicConfig(level=logging.INFO)


async def main():
    auth = EvnexAuth()
    await auth.start_authentication(
        os.environ["EVNEX_CLIENT_USERNAME"], os.environ["EVNEX_CLIENT_PASSWORD"]
    )
    evnex = Evnex(auth=auth)

    user_data = await evnex.get_user_detail()

    print("User:", user_data.name, user_data.email, user_data.id)

    for org in user_data.organisations:
        print("Getting charge points for", org.name)
        charge_points = await evnex.get_org_charge_points(org_id=org.id)

        for charge_point in charge_points:
            detail = await evnex.get_charge_point_detail_v3(
                charge_point_id=charge_point.id
            )
            print(detail)
            await evnex.disable_charger(org_id=org.id, charge_point_id=charge_point.id)
            await asyncio.sleep(5)
            detail = await evnex.get_charge_point_detail_v3(
                charge_point_id=charge_point.id
            )
            print(detail)

            print("Renabling")
            await evnex.enable_charger(org_id=org.id, charge_point_id=charge_point.id)

            # Safe to repeat
            await evnex.enable_charger(org_id=org.id, charge_point_id=charge_point.id)


if __name__ == "__main__":
    asyncio.run(main())
