def read_one(factory, query):
    connection = factory()
    try:
        with connection.cursor() as cursor:
            cursor.execute(query)
            return cursor.fetchone()
    finally:
        connection.close()
