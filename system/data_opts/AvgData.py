import datetime

import calendar


class AvgData:
    def __init__(self):
        self.hour = []
        self.month = []
        self.year = []
        self.hour_create_time = int(datetime.datetime.now().timestamp())
        self.hour_flag = False
        self.hour_length = 60 * 60
        self.get_month_start_end()
        self.get_year_start_end()

    def get_month_start_end(self):
        now = datetime.datetime.now()
        self.month_start = int(datetime.datetime(now.year, now.month, 1).timestamp())
        self.month_end = int((datetime.datetime(now.year, now.month,
                                                calendar.monthrange(now.year, now.month)[1]) + datetime.timedelta(
            hours=23, minutes=59, seconds=59)).timestamp())

    def get_year_start_end(self):
        now = datetime.datetime.now()
        self.year_start = int(datetime.datetime(now.year, 1, 1).timestamp())
        self.year_end = int((datetime.datetime(now.year, 12, calendar.monthrange(now.year, 12)[1]) + datetime.timedelta(
            hours=23, minutes=59, seconds=59)).timestamp())

    def get_value(self, value):
        now_date = int(datetime.datetime.now().timestamp())
        hour = self.get_hour_avg(value, now_date)
        month = self.get_month_avg(value, now_date)
        year = self.get_year_avg(value, now_date)
        return hour, month, year

    def get_hour_avg(self, value, now_time):
        if self.hour_create_time + self.hour_length >= now_time:
            if self.hour_flag == False:
                self.hour.append(value)
            else:
                self.hour.__delitem__(0)
                self.hour.append(value)

        else:
            self.hour_create_time = now_time
            self.hour_flag = True

        return sum(self.hour) / len(self.hour)

    def get_month_avg(self, value, now_time):
        if self.month_start <= now_time and now_time <= self.month_end:
            self.month.append(value)
        else:
            self.month.clear()
            self.month.append(value)
            self.get_month_start_end()
        # print(self.month)
        return sum(self.month) / len(self.month)

    def get_year_avg(self, value, now_time):
        if self.year_start <= now_time and now_time <= self.year_end:
            self.year.append(value)
        else:
            self.year.clear()
            self.year.append(value)
            self.get_year_start_end()
        # print(self.year)

        return sum(self.year) / len(self.year)
