import asyncio
import os

from evnex.api import Evnex
from evnex.auth import EvnexAuth


async def main():
    auth = EvnexAuth()
    await auth.start_authentication(
        os.environ["EVNEX_CLIENT_USERNAME"], os.environ["EVNEX_CLIENT_PASSWORD"]
    )
    evnex = Evnex(auth=auth)

    user_data = await evnex.get_user_detail()

    print("User:", user_data.name, user_data.email, user_data.id)

    user_data = await evnex.get_org_charge_points(evnex.org_id)

    for chargepoint in user_data:
        # print("Global connector statuses")
        # status = await evnex.get_org_summary_status(org_id=org.slug)
        # print(status)
        print(chargepoint.id)
        if chargepoint.connectors:
            for connector in chargepoint.connectors:
                print(connector.connectorType)
                print(connector.status)
                print(connector.evseId)


if __name__ == "__main__":
    asyncio.run(main())
