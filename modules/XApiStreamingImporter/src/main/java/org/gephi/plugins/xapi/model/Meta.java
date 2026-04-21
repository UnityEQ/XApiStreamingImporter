package org.gephi.plugins.xapi.model;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;

@JsonIgnoreProperties(ignoreUnknown = true)
public class Meta {

    @JsonProperty("newest_id")
    public String newestId;

    @JsonProperty("oldest_id")
    public String oldestId;

    @JsonProperty("result_count")
    public int resultCount;

    @JsonProperty("next_token")
    public String nextToken;
}
