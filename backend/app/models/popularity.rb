class Popularity
  SORT_ASCENDING = 1
  SORT_DESCENDING = -1

  def self.most_popular(limit, start_index)
    sort_by_popularity(SORT_DESCENDING, limit, start_index)
  end

  def self.least_popular(limit, start_index)
    sort_by_popularity(SORT_ASCENDING, limit, start_index)
  end

  def self.total_symbols(hours_ago = nil)
    MongoClient[:popularity].aggregate([
      hours_ago && { "$match" => { timestamp: { "$gte": hours_ago.hours.ago.utc } } },
      { "$group" => { _id: "$instrument_id" } },
      { "$count" => "total_symbols" },
    ].compact).first
  end

  def self.sort_by_popularity(sort_direction, limit, start_index)
    MongoClient[:popularity].aggregate([
      { "$match" => { timestamp: { "$gte": 2.hour.ago.utc } } },
      { "$sort" => { timestamp: -1 } },
      { "$group" => { _id: "$instrument_id", latest_popularity: { "$first" => "$popularity" } } },
      { "$lookup" => {
        from: "index",
        localField: "_id",
        foreignField: "instrument_id",
        as: "indexes",
      } },
      { "$addFields" => { symbol: { "$arrayElemAt" => ["$indexes.symbol", 0] } } },
      { "$sort" => { latest_popularity: sort_direction, symbol: SORT_ASCENDING } },
      { "$skip" => start_index },
      { "$limit" => limit },
    ])
  end

  def self.get_ranking(symbol)
    MongoClient[:popularity].aggregate([
      { "$match" => { timestamp: { "$gte": 2.hour.ago.utc } } },
      { "$sort" => { timestamp: -1 } },
      { "$group" => { _id: "$instrument_id", latest_popularity: { "$first" => "$popularity" } } },
      { "$lookup" => {
        from: "index",
        localField: "_id",
        foreignField: "instrument_id",
        as: "indexes",
      } },
      { "$addFields" => { symbol: { "$arrayElemAt" => ["$indexes.symbol", 0] } } },
      { "$sort" => { latest_popularity: SORT_DESCENDING, symbol: SORT_ASCENDING } },
      { "$group" => { _id: 1, symbol: { "$push" => "$symbol" } } },
      { "$unwind" => { path: "$symbol", includeArrayIndex: "ranking" } },
      { "$match" => { symbol: symbol } },
      { "$addFields" => { ranking: { "$add" => ["$ranking", 1] } } },
      { "$limit" => 1 },
    ]).first
  end

  def self.get_history_for_symbol(symbol)
    entry = MongoClient[:index].aggregate([
      { "$match" => { symbol: symbol } },
      { "$lookup" => {
        from: "popularity",
        localField: "instrument_id",
        foreignField: "instrument_id",
        as: "popularity_history",
      } },
      { "$limit" => 1 },
    ]).first
    entry && entry["popularity_history"]
  end

  def self.largest_popularity_changes(options)
    options = options.merge(sort_direction: SORT_DESCENDING, take_absoute_value: true)
    popularity_difference_lookup(options)
  end

  def self.largest_popularity_decreases(options)
    popularity_difference_lookup(options.merge(sort_direction: SORT_ASCENDING))
  end

  def self.largest_popularity_increases(options)
    popularity_difference_lookup(options.merge(sort_direction: SORT_DESCENDING))
  end

  def self.popularity_difference_lookup(options)
    hours_ago          = options[:hours_ago]
    sort_direction     = options[:sort_direction]
    limit              = options[:limit]
    take_absoute_value = options[:take_absoute_value]
    percentage         = options[:percentage]
    min_popularity     = options[:min_popularity]
    start_index        = options[:start_index]

    if percentage
      difference_query = { "$cond" => [
        { "$eq" => ["$start_popularity", 0] },
        nil,
        { "$multiply" => [100, { "$divide" => [{ "$subtract" => ["$end_popularity", "$start_popularity"] }, "$start_popularity"] }] },
      ]}
    else
      difference_query = { "$subtract" => ["$end_popularity", "$start_popularity"] }
    end

    if take_absoute_value
      sort_field = :abs_popularity_difference
      sorter = { "$sort" => { diff_is_null: SORT_ASCENDING, abs_popularity_difference: sort_direction, symbol: SORT_ASCENDING } }
    else
      sort_field = :popularity_difference
      sorter = { "$sort" => { diff_is_null: SORT_ASCENDING, popularity_difference: sort_direction, symbol: SORT_ASCENDING } }
    end

    MongoClient[:popularity].aggregate([
      { "$match" => { timestamp: { "$gte" => hours_ago.hour.ago.utc } } },
      { "$sort" => { timestamp: -1 } },
      { "$group" => {
        _id: "$instrument_id",
        end_popularity: { "$first" => "$popularity" },
        start_popularity: { "$last" => "$popularity" },
      } },
      min_popularity && { "$match" => { start_popularity: { "$gte" => min_popularity } } },
      { "$lookup" => {
        from: "index",
        localField: "_id",
        foreignField: "instrument_id",
        as: "indexes",
      } },
      { "$addFields" => { symbol: { "$arrayElemAt" => ["$indexes.symbol", 0] } } },
      { "$addFields" => { popularity_difference: difference_query } },
      { "$addFields" => { diff_is_null: { "$eq" => [{ "$type" => "$#{sort_field}" }, "null"] } } },
      take_absoute_value && { "$addFields" => { abs_popularity_difference: { "$abs" => "$popularity_difference" } } },
      sorter,
      { "$skip" => start_index },
      { "$limit" => limit },
    ].compact)
  end

  def self.bucket_popularity(bucket_count)
    entries = MongoClient[:popularity].aggregate([
      { "$match": { timestamp: { "$gte": 2.hours.ago.utc } } },
      { "$sort": { timestamp: -1 } },
      { "$group": { _id: "$instrument_id", latest_popularity: { "$first": "$popularity" } } },
    ])
  end
end
