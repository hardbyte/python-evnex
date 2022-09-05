from pydantic import BaseSettings, SecretStr

from evnex.auth import retrieve_auth_token
from evnex.charge_points import get_charge_point_detail, get_org_charge_points
from evnex.user import get_user_detail


class EvnexAuthDetails(BaseSettings):
    EVNEX_CLIENT_USERNAME: str
    EVNEX_CLIENT_PASSWORD: SecretStr


def main():
    creds = EvnexAuthDetails()
    token = retrieve_auth_token(username=creds.EVNEX_CLIENT_USERNAME,
                                password=creds.EVNEX_CLIENT_PASSWORD.get_secret_value())

    user_data = get_user_detail(token)

    print("User:", user_data.name, user_data.email, user_data.id)

    for org in user_data.organisations:
        print("Getting charge points for", org.name)
        charge_points = get_org_charge_points(token=token, org_id=org.id)
        for charge_point in charge_points:
            print(charge_point.name, charge_point.networkStatus, charge_point.serial, charge_point.id)

            print(get_charge_point_detail(token=token, charge_point_id=charge_point.id))

if __name__ == '__main__':
    main()